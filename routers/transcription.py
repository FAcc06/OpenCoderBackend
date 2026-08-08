"""
Transcription router
  POST   /{project_id}/tasks/{task_id}/transcription           - trigger (manager)
  GET    /{project_id}/tasks/{task_id}/transcription/status    - status
  GET    /{project_id}/tasks/{task_id}/transcript              - active transcript
  GET    /{project_id}/tasks/{task_id}/transcripts             - all versions
  POST   /{project_id}/tasks/{task_id}/transcription/retry     - retry failed (manager)
  POST   /{project_id}/tasks/{task_id}/transcription/regenerate- force new (manager)
  POST   /{project_id}/tasks/{task_id}/transcript-codings      - save coding
  GET    /{project_id}/tasks/{task_id}/transcript-codings      - list codings
  DELETE /{project_id}/tasks/{task_id}/transcript-codings/{id} - delete coding
"""

from datetime import datetime
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query

from database import get_core_db, get_project_db
from models import LabelOption, TranscriptCodingCreate
from routers.auth import verify_token

router = APIRouter()


# ── Shared helpers ────────────────────────────────────────────────────────────

def _decode_token(token: str) -> dict:
    try:
        return verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


async def _get_project_and_task(project_id: str, task_id: str):
    """Returns (core_db, project_db, project_doc, task_doc, project_oid, task_oid)."""
    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        project_oid = ObjectId(project_id)
        task_oid    = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")

    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_db = await get_project_db(project_id)
    task = await project_db.tasks.find_one({"_id": task_oid})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return core_db, project_db, project, task, project_oid, task_oid


async def _require_manager(core_db, project, user_id: str):
    """Raise 403 if user is not project owner or membership manager."""
    from services.membership_service import is_project_manager
    try:
        user_oid = ObjectId(user_id)
        project_oid = project["_id"]
        if await is_project_manager(core_db, user_oid, project_oid):
            return
    except Exception:
        pass
    manager_id = project.get("owner_user_id")
    if not manager_id or str(manager_id) != user_id:
        raise HTTPException(status_code=403, detail="Only the project manager can perform this action")


def _str_id(v) -> Optional[str]:
    return str(v) if v is not None else None


def _serialize_transcript(doc: dict) -> dict:
    """Convert a MongoDB transcript document to a JSON-safe dict."""
    return {
        "id":               _str_id(doc.get("_id")),
        "task_id":          _str_id(doc.get("task_id")),
        "project_id":       _str_id(doc.get("project_id")),
        "version":          doc.get("version", 1),
        "provider":         doc.get("provider", "openrouter"),
        "model":            doc.get("model", ""),
        "language":         doc.get("language"),
        "duration_seconds": doc.get("duration_seconds"),
        "text":             doc.get("text", ""),
        "segments":         doc.get("segments", []),
        "words":            doc.get("words", []),
        "summary":          doc.get("summary"),
        "summary_model":    doc.get("summary_model"),
        "created_at":       doc.get("created_at", datetime.utcnow()).isoformat(),
    }


def _serialize_coding(doc: dict) -> dict:
    return {
        "id":           _str_id(doc.get("_id")),
        "task_id":      _str_id(doc.get("task_id")),
        "coder_user_id": _str_id(doc.get("coder_user_id")),
        "labels":       doc.get("labels", []),
        "notes":        doc.get("notes"),
        "target":       doc.get("target", {}),
        "created_at":   doc.get("created_at", datetime.utcnow()).isoformat(),
    }


# ── Trigger transcription ─────────────────────────────────────────────────────

@router.post("/{project_id}/tasks/{task_id}/transcription")
async def trigger_transcription(
    project_id: str,
    task_id:    str,
    token:      str = Query(...),
):
    """
    Manager-only: queue a transcription job for an audio or video task.
    Idempotent: returns existing job info if already queued/processing.
    """
    user_data = _decode_token(token)
    user_id   = user_data.get("sub")

    core_db, project_db, project, task, _, task_oid = await _get_project_and_task(
        project_id, task_id
    )
    await _require_manager(core_db, project, user_id)

    # Only valid for audio/video tasks
    task_type = task.get("task_type", "")
    if task_type not in ("audio", "video"):
        raise HTTPException(
            status_code=400,
            detail=f"Transcription is only supported for audio/video tasks (task_type={task_type})",
        )

    # Check existing active job
    existing = await core_db.transcription_jobs.find_one(
        {
            "task_id":   task_oid,
            "project_id": ObjectId(project_id),
            "status":    {"$in": ["queued", "processing"]},
        }
    )
    if existing:
        return {
            "success":    True,
            "queued":     False,
            "job_id":     _str_id(existing["_id"]),
            "job_status": existing["status"],
            "message":    "Transcription job already active",
        }

    # Create new job
    now = datetime.utcnow()
    job_doc = {
        "task_id":     task_oid,
        "project_id":  ObjectId(project_id),
        "status":      "queued",
        "attempt_count": 0,
        "max_attempts":  3,
        "locked_at":   None,
        "locked_by":   None,
        "error_code":  None,
        "error_message": None,
        "transcript_id": None,
        "created_by":  ObjectId(user_id),
        "created_at":  now,
        "started_at":  None,
        "completed_at": None,
        "updated_at":  now,
    }
    result = await core_db.transcription_jobs.insert_one(job_doc)

    # Update task status
    await project_db.tasks.update_one(
        {"_id": task_oid},
        {"$set": {"transcription_status": "queued", "updated_at": now}},
    )

    return {
        "success":    True,
        "queued":     True,
        "job_id":     _str_id(result.inserted_id),
        "job_status": "queued",
        "message":    "Transcription job queued successfully",
    }


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/{project_id}/tasks/{task_id}/transcription/status")
async def get_transcription_status(
    project_id: str,
    task_id:    str,
    token:      str = Query(...),
):
    """Return current transcription status for a task."""
    _decode_token(token)
    core_db, project_db, project, task, _, task_oid = await _get_project_and_task(
        project_id, task_id
    )

    status = task.get("transcription_status", "none")
    active_id = task.get("active_transcript_id")
    version   = task.get("active_transcript_version")

    # Latest job info
    latest_job = await core_db.transcription_jobs.find_one(
        {"task_id": task_oid, "project_id": ObjectId(project_id)},
        sort=[("created_at", -1)],
    )

    job_info = None
    if latest_job:
        job_info = {
            "id":            _str_id(latest_job["_id"]),
            "status":        latest_job["status"],
            "attempt_count": latest_job.get("attempt_count", 0),
            "error_message": latest_job.get("error_message"),
            "created_at":    latest_job["created_at"].isoformat(),
        }

    return {
        "transcription_status":      status,
        "active_transcript_id":      _str_id(active_id),
        "active_transcript_version": version,
        "latest_job":                job_info,
    }


# ── Get active transcript ─────────────────────────────────────────────────────

@router.get("/{project_id}/tasks/{task_id}/transcript")
async def get_active_transcript(
    project_id: str,
    task_id:    str,
    token:      str = Query(...),
):
    """Return the active (latest) transcript for a task."""
    _decode_token(token)
    core_db, project_db, project, task, _, task_oid = await _get_project_and_task(
        project_id, task_id
    )

    active_id = task.get("active_transcript_id")
    if not active_id:
        raise HTTPException(status_code=404, detail="No transcript available yet")

    transcript = await project_db.transcripts.find_one({"_id": active_id})
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    return _serialize_transcript(transcript)


# ── Generate / refresh summary for existing transcript ────────────────────────

@router.post("/{project_id}/tasks/{task_id}/transcript/summary")
async def generate_transcript_summary(
    project_id: str,
    task_id:    str,
    token:      str = Query(...),
):
    """
    Generate (or regenerate) an AI summary for the active transcript without
    re-running the full speech-to-text pipeline.
    """
    _decode_token(token)
    core_db, project_db, project, task, _, task_oid = await _get_project_and_task(
        project_id, task_id
    )

    active_id = task.get("active_transcript_id")
    if not active_id:
        raise HTTPException(status_code=404, detail="No transcript available yet")

    transcript = await project_db.transcripts.find_one({"_id": active_id})
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    text = transcript.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Transcript has no text to summarise")

    from services.transcription_service import generate_summary, SUMMARY_MODEL
    try:
        summary_text = await generate_summary(text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summary generation failed: {e}")

    now = datetime.utcnow()
    await project_db.transcripts.update_one(
        {"_id": active_id},
        {"$set": {"summary": summary_text, "summary_model": SUMMARY_MODEL, "updated_at": now}},
    )

    return {
        "success": True,
        "summary": summary_text,
        "summary_model": SUMMARY_MODEL,
    }


# ── List transcript versions ──────────────────────────────────────────────────

@router.get("/{project_id}/tasks/{task_id}/transcripts")
async def list_transcripts(
    project_id: str,
    task_id:    str,
    token:      str = Query(...),
):
    """Return all transcript versions for a task (newest first)."""
    _decode_token(token)
    _, project_db, _project, task, _, task_oid = await _get_project_and_task(
        project_id, task_id
    )

    docs = await project_db.transcripts.find(
        {"task_id": task_oid}
    ).sort("version", -1).to_list(length=20)

    active_id = task.get("active_transcript_id")

    return {
        "transcripts": [
            {**_serialize_transcript(d), "is_active": str(d["_id"]) == str(active_id)}
            for d in docs
        ]
    }


# ── Retry failed job ──────────────────────────────────────────────────────────

@router.post("/{project_id}/tasks/{task_id}/transcription/retry")
async def retry_transcription(
    project_id: str,
    task_id:    str,
    token:      str = Query(...),
):
    """Manager-only: re-queue a failed transcription job."""
    user_data = _decode_token(token)
    user_id   = user_data.get("sub")

    core_db, project_db, project, task, _, task_oid = await _get_project_and_task(
        project_id, task_id
    )
    await _require_manager(core_db, project, user_id)

    latest_job = await core_db.transcription_jobs.find_one(
        {"task_id": task_oid, "project_id": ObjectId(project_id)},
        sort=[("created_at", -1)],
    )

    if not latest_job:
        raise HTTPException(status_code=404, detail="No transcription job found")

    if latest_job["status"] not in ("failed",):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry a job in status '{latest_job['status']}'",
        )

    now = datetime.utcnow()
    await core_db.transcription_jobs.update_one(
        {"_id": latest_job["_id"]},
        {
            "$set": {
                "status":        "queued",
                "attempt_count": 0,
                "locked_at":     None,
                "locked_by":     None,
                "error_code":    None,
                "error_message": None,
                "updated_at":    now,
            }
        },
    )
    await project_db.tasks.update_one(
        {"_id": task_oid},
        {"$set": {"transcription_status": "queued", "updated_at": now}},
    )

    return {"success": True, "message": "Transcription job re-queued"}


# ── Regenerate (force new version) ───────────────────────────────────────────

@router.post("/{project_id}/tasks/{task_id}/transcription/regenerate")
async def regenerate_transcription(
    project_id: str,
    task_id:    str,
    token:      str = Query(...),
):
    """
    Manager-only: create a brand-new transcription job regardless of
    existing status. Existing transcripts are preserved (versioned).
    """
    user_data = _decode_token(token)
    user_id   = user_data.get("sub")

    core_db, project_db, project, task, _, task_oid = await _get_project_and_task(
        project_id, task_id
    )
    await _require_manager(core_db, project, user_id)

    task_type = task.get("task_type", "")
    if task_type not in ("audio", "video"):
        raise HTTPException(status_code=400, detail="Only audio/video tasks support transcription")

    now = datetime.utcnow()
    job_doc = {
        "task_id":       task_oid,
        "project_id":    ObjectId(project_id),
        "status":        "queued",
        "attempt_count": 0,
        "max_attempts":  3,
        "locked_at":     None,
        "locked_by":     None,
        "error_code":    None,
        "error_message": None,
        "transcript_id": None,
        "created_by":    ObjectId(user_id),
        "created_at":    now,
        "started_at":    None,
        "completed_at":  None,
        "updated_at":    now,
    }
    result = await core_db.transcription_jobs.insert_one(job_doc)

    await project_db.tasks.update_one(
        {"_id": task_oid},
        {"$set": {"transcription_status": "queued", "updated_at": now}},
    )

    return {
        "success":  True,
        "job_id":   _str_id(result.inserted_id),
        "message":  "New transcription job queued (previous transcripts preserved)",
    }


# ── Transcript codings ────────────────────────────────────────────────────────

@router.post("/{project_id}/tasks/{task_id}/transcript-codings")
async def create_transcript_coding(
    project_id: str,
    task_id:    str,
    body:       TranscriptCodingCreate,
    token:      str = Query(...),
):
    """Save a transcript segment/word-range coding for a coder."""
    user_data = _decode_token(token)
    user_id   = user_data.get("sub")

    core_db, project_db, _project, task, _, task_oid = await _get_project_and_task(
        project_id, task_id
    )

    # Validate target
    target = body.target
    if not isinstance(target, dict):
        raise HTTPException(status_code=400, detail="target must be an object")
    if target.get("type") not in ("segment_range", "word_range"):
        raise HTTPException(status_code=400, detail="target.type must be segment_range or word_range")
    if not target.get("transcript_id"):
        raise HTTPException(status_code=400, detail="target.transcript_id is required")

    now = datetime.utcnow()
    doc = {
        "task_id":       task_oid,
        "project_id":    ObjectId(project_id),
        "coder_user_id": ObjectId(user_id),
        "labels":        [l.dict() for l in body.labels],
        "notes":         body.notes,
        "target":        target,
        "created_at":    now,
        "updated_at":    now,
    }
    result = await project_db.transcript_codings.insert_one(doc)

    return {"success": True, "coding_id": _str_id(result.inserted_id)}


@router.get("/{project_id}/tasks/{task_id}/transcript-codings")
async def list_transcript_codings(
    project_id:  str,
    task_id:     str,
    token:       str = Query(...),
    coder_only:  bool = Query(False, description="Return only codings by the requesting user"),
):
    """List transcript codings for a task."""
    user_data = _decode_token(token)
    user_id   = user_data.get("sub")

    _, project_db, _project, _task, _, task_oid = await _get_project_and_task(
        project_id, task_id
    )

    query: dict = {"task_id": task_oid}
    if coder_only:
        query["coder_user_id"] = ObjectId(user_id)

    docs = await project_db.transcript_codings.find(query).sort("created_at", 1).to_list(length=1000)
    return {"codings": [_serialize_coding(d) for d in docs]}


@router.delete("/{project_id}/tasks/{task_id}/transcript-codings/{coding_id}")
async def delete_transcript_coding(
    project_id: str,
    task_id:    str,
    coding_id:  str,
    token:      str = Query(...),
):
    """Delete a transcript coding (owner or manager)."""
    user_data = _decode_token(token)
    user_id   = user_data.get("sub")

    core_db, project_db, project, _task, project_oid, task_oid = await _get_project_and_task(
        project_id, task_id
    )

    try:
        coding_oid = ObjectId(coding_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid coding_id")

    coding = await project_db.transcript_codings.find_one({"_id": coding_oid, "task_id": task_oid})
    if not coding:
        raise HTTPException(status_code=404, detail="Coding not found")

    # Allow coding author or project manager (owner / membership)
    is_owner = str(coding.get("coder_user_id")) == user_id
    from services.membership_service import is_project_manager
    try:
        user_oid = ObjectId(user_id)
        is_manager = await is_project_manager(core_db, user_oid, project_oid)
    except Exception:
        is_manager = str(project.get("owner_user_id")) == user_id
    if not (is_owner or is_manager):
        raise HTTPException(status_code=403, detail="Not authorized to delete this coding")

    await project_db.transcript_codings.delete_one({"_id": coding_oid})
    return {"success": True}
