"""
Transcription service:
  - MongoDB-backed job queue (core_db.transcription_jobs)
  - Drive file download via httpx (async, no blocking SDK calls)
  - OpenRouter Whisper transcription
  - Result normalisation + save to project_db.transcripts
  - Same-process asyncio worker started from FastAPI lifespan
"""

import asyncio
import logging
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from typing import Optional

import httpx
from bson import ObjectId

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
OPENROUTER_API_KEY   = os.getenv("OPENROUTER_API_KEY", "")
WHISPER_MODEL        = os.getenv("WHISPER_MODEL", "openai/whisper-large-v3-turbo")
WORKER_POLL_INTERVAL = int(os.getenv("TRANSCRIPTION_POLL_INTERVAL", "10"))   # seconds
LOCK_TIMEOUT_MINUTES = int(os.getenv("TRANSCRIPTION_LOCK_TIMEOUT", "30"))
MAX_ATTEMPTS         = int(os.getenv("TRANSCRIPTION_MAX_ATTEMPTS", "3"))
WORKER_ID            = f"worker-{uuid.uuid4().hex[:8]}"
TEMP_DIR             = os.getenv("TRANSCRIPTION_TEMP_DIR", tempfile.gettempdir())

OPENROUTER_BASE_URL  = "https://openrouter.ai/api/v1"
GOOGLE_TOKEN_URL     = "https://oauth2.googleapis.com/token"
DRIVE_DOWNLOAD_URL   = "https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"

# Free open-source LLM for summarisation (override via env)
SUMMARY_MODEL        = os.getenv(
    "SUMMARY_MODEL",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
)
SUMMARY_MAX_CHARS    = 12_000   # truncate very long transcripts before sending
SUMMARY_MAX_TOKENS   = 400


# ── Helper: refresh Google access token ─────────────────────────────────────
async def refresh_google_access_token(refresh_token: str) -> str:
    """Exchange a Google refresh_token for a fresh access_token."""
    client_id     = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type":    "refresh_token",
                "refresh_token": refresh_token,
                "client_id":     client_id,
                "client_secret": client_secret,
            },
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to refresh Google token: {response.status_code} {response.text}"
        )
    data = response.json()
    return data["access_token"]


# ── Helper: download a Drive file ────────────────────────────────────────────
async def download_drive_file(
    file_id: str,
    access_token: str,
    refresh_token: Optional[str],
    dest_path: str,
) -> str:
    """
    Stream-download a Google Drive file to `dest_path`.
    Retries once with a fresh token if we get 401.
    Returns dest_path on success.
    """
    url = DRIVE_DOWNLOAD_URL.format(file_id=file_id)
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)

        # Token expired → refresh and retry once
        if response.status_code == 401 and refresh_token:
            logger.info("Access token expired, refreshing and retrying download…")
            access_token = await refresh_google_access_token(refresh_token)
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(url, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(
                f"Drive download failed: {response.status_code} {response.text[:200]}"
            )

        os.makedirs(os.path.dirname(dest_path) if os.path.dirname(dest_path) else TEMP_DIR, exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(response.content)

    return dest_path


# ── Helper: generate summary via free open-source LLM ───────────────────────
async def generate_summary(transcript_text: str) -> str:
    """
    Call a free open-source LLM on OpenRouter to summarise the transcript.
    Returns the summary string.  Raises on failure (caller should catch).
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not configured")

    text_slice = transcript_text[:SUMMARY_MAX_CHARS]

    prompt = (
        "You are a research assistant helping qualitative researchers. "
        "Below is a transcript from a recorded interview or presentation.\n\n"
        "Please provide:\n"
        "1. A concise summary (3-4 sentences) of the main content.\n"
        "2. 3-5 key themes or topics discussed.\n\n"
        "Respond in the same language as the transcript. "
        "Be factual and neutral.\n\n"
        f"Transcript:\n{text_slice}"
    )

    def _sync_call() -> str:
        import requests as _req

        response = _req.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":      SUMMARY_MODEL,
                "messages":   [{"role": "user", "content": prompt}],
                "max_tokens": SUMMARY_MAX_TOKENS,
            },
            timeout=90,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"LLM summary error: {response.status_code} {response.text[:300]}"
            )
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    return await asyncio.to_thread(_sync_call)


# ── Helper: call OpenRouter Whisper ─────────────────────────────────────────
async def call_whisper(file_path: str, filename: str, mime_type: str) -> dict:
    """
    POST to OpenRouter /v1/audio/transcriptions.
    Uses `requests` (not httpx) to avoid httpx 0.28 async/sync issues,
    wrapped in asyncio.to_thread so the event loop stays unblocked.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    url = f"{OPENROUTER_BASE_URL}/audio/transcriptions"

    def _sync_whisper() -> dict:
        import requests as _req

        # Pass a file object (not bytes) so requests streams from disk
        # and never loads the whole file into RAM at once.
        with open(file_path, "rb") as f:
            response = _req.post(
                url,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                files={"file": (filename, f, mime_type)},
                data=[
                    ("model",                     WHISPER_MODEL),
                    ("response_format",           "verbose_json"),
                    ("timestamp_granularities[]", "segment"),
                    ("timestamp_granularities[]", "word"),
                ],
                timeout=600,
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"OpenRouter Whisper error: {response.status_code} {response.text[:500]}"
            )
        return response.json()

    # Run the blocking HTTP call in a thread pool so the event loop stays free
    return await asyncio.to_thread(_sync_whisper)


# ── Helper: normalise Whisper response ──────────────────────────────────────
def normalize_transcript(raw: dict) -> dict:
    """
    Convert OpenAI verbose_json to our internal schema:
    { language, duration_seconds, text, segments: [...], words: [...] }
    """
    segments_raw = raw.get("segments") or []
    words_raw    = raw.get("words") or []

    segments = []
    for seg in segments_raw:
        idx = int(seg.get("id", len(segments)))
        segments.append({
            "index": idx,
            "start": float(seg.get("start", 0)),
            "end":   float(seg.get("end", 0)),
            "text":  seg.get("text", "").strip(),
        })

    words = []
    for w in words_raw:
        words.append({
            "index": len(words),
            "start": float(w.get("start", 0)),
            "end":   float(w.get("end", 0)),
            "text":  w.get("word", "").strip(),
        })

    # Back-fill start_word_index / end_word_index onto segments
    if words:
        seg_cursor = 0
        for wi, word in enumerate(words):
            while seg_cursor < len(segments) and segments[seg_cursor]["end"] < word["start"] - 0.05:
                seg_cursor += 1
            if seg_cursor < len(segments):
                words[wi]["segment_index"] = seg_cursor
                seg = segments[seg_cursor]
                if "start_word_index" not in seg:
                    seg["start_word_index"] = wi
                seg["end_word_index"] = wi

    return {
        "language":         raw.get("language"),
        "duration_seconds": raw.get("duration"),
        "text":             raw.get("text", "").strip(),
        "segments":         segments,
        "words":            words,
    }


# ── Job-claiming (atomic find-and-modify) ───────────────────────────────────
async def claim_next_job(core_db) -> Optional[dict]:
    """Atomically claim the oldest queued job (or a stale locked one)."""
    now       = datetime.utcnow()
    lock_time = now - timedelta(minutes=LOCK_TIMEOUT_MINUTES)

    job = await core_db.transcription_jobs.find_one_and_update(
        {
            "$or": [
                {"status": "queued"},
                {
                    "status":    "processing",
                    "locked_at": {"$lt": lock_time},
                },
            ],
            "attempt_count": {"$lt": MAX_ATTEMPTS},
        },
        {
            "$set": {
                "status":    "processing",
                "locked_at": now,
                "locked_by": WORKER_ID,
                "updated_at": now,
            },
            "$inc": {"attempt_count": 1},
        },
        sort=[("created_at", 1)],
        return_document=True,
    )
    return job


# ── Main job processor ────────────────────────────────────────────────────────
async def process_transcription_job(job: dict, core_db, get_project_db_fn) -> None:
    """
    Full lifecycle of one transcription job:
    download → whisper → normalise → save transcript → update task → mark done
    """
    from database import get_core_db  # local import to avoid circular

    job_id     = job["_id"]
    task_oid   = job["task_id"]
    project_id = str(job["project_id"])
    created_by = job["created_by"]

    logger.info("Processing transcription job %s for task %s", job_id, task_oid)

    async def fail(code: str, message: str):
        now = datetime.utcnow()
        attempt = job.get("attempt_count", 1)
        new_status = "failed" if attempt >= MAX_ATTEMPTS else "queued"
        await core_db.transcription_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status":        new_status,
                    "locked_at":     None,
                    "locked_by":     None,
                    "error_code":    code,
                    "error_message": message,
                    "updated_at":    now,
                }
            },
        )
        if new_status == "failed":
            project_db = await get_project_db_fn(project_id)
            await project_db.tasks.update_one(
                {"_id": task_oid},
                {"$set": {"transcription_status": "failed", "updated_at": now}},
            )
        logger.error("Job %s failed [%s]: %s", job_id, code, message)

    temp_path = None
    try:
        # 1. Load project and manager credentials
        project = await core_db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            return await fail("NO_PROJECT", f"Project {project_id} not found")

        manager_id = project.get("owner_user_id")
        manager    = await core_db.users.find_one({"_id": manager_id})
        if not manager:
            return await fail("NO_MANAGER", "Project manager not found")

        google_creds = manager.get("google_credentials") or {}
        access_token  = google_creds.get("access_token")
        refresh_token = google_creds.get("refresh_token")
        if not access_token:
            return await fail("NO_CREDS", "Manager has no Google credentials")

        # 2. Load task → get drive_file_id + mime_type
        project_db = await get_project_db_fn(project_id)
        task = await project_db.tasks.find_one({"_id": task_oid})
        if not task:
            return await fail("NO_TASK", f"Task {task_oid} not found")

        payload = task.get("payload") or {}
        media_data = payload.get("audio") or payload.get("video")
        if not media_data:
            return await fail("NO_MEDIA", "Task has no audio or video payload")

        drive_file_id = media_data.get("drive_file_id")
        mime_type     = media_data.get("mime_type") or "audio/mpeg"
        filename      = media_data.get("original_filename") or "media"

        if not drive_file_id:
            return await fail("NO_DRIVE_ID", "Task media has no drive_file_id")

        # 3. Download from Drive
        ext       = os.path.splitext(filename)[1] or ".mp3"
        temp_path = os.path.join(TEMP_DIR, f"txjob_{uuid.uuid4().hex}{ext}")
        logger.info("Downloading Drive file %s → %s", drive_file_id, temp_path)

        await download_drive_file(
            file_id=drive_file_id,
            access_token=access_token,
            refresh_token=refresh_token,
            dest_path=temp_path,
        )

        # 4. Call Whisper
        logger.info("Calling Whisper for job %s", job_id)
        raw_result = await call_whisper(temp_path, filename, mime_type)

        # 5. Normalise
        normalized = normalize_transcript(raw_result)

        # 6. Determine version number
        last = await project_db.transcripts.find_one(
            {"task_id": task_oid},
            sort=[("version", -1)],
        )
        version = (last["version"] + 1) if last else 1

        # 7. Save transcript
        now = datetime.utcnow()
        transcript_doc = {
            "task_id":          task_oid,
            "project_id":       ObjectId(project_id),
            "version":          version,
            "provider":         "openrouter",
            "model":            WHISPER_MODEL,
            "language":         normalized["language"],
            "duration_seconds": normalized["duration_seconds"],
            "text":             normalized["text"],
            "segments":         normalized["segments"],
            "words":            normalized["words"],
            "created_by":       created_by,
            "created_at":       now,
            "updated_at":       now,
        }
        result = await project_db.transcripts.insert_one(transcript_doc)
        transcript_id = result.inserted_id

        # 8. Generate summary (non-fatal — transcription still succeeds if this fails)
        try:
            logger.info("Generating summary for transcript %s", transcript_id)
            summary_text = await generate_summary(normalized["text"])
            await project_db.transcripts.update_one(
                {"_id": transcript_id},
                {"$set": {
                    "summary":       summary_text,
                    "summary_model": SUMMARY_MODEL,
                }},
            )
            logger.info("Summary saved for transcript %s", transcript_id)
        except Exception as sum_err:
            logger.warning("Summary generation failed (non-fatal): %s", sum_err)

        # 9. Update task transcription_status
        await project_db.tasks.update_one(
            {"_id": task_oid},
            {
                "$set": {
                    "transcription_status":    "completed",
                    "active_transcript_id":    transcript_id,
                    "active_transcript_version": version,
                    "updated_at":              now,
                }
            },
        )

        # 9. Mark job done
        await core_db.transcription_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status":        "completed",
                    "transcript_id": transcript_id,
                    "locked_at":     None,
                    "locked_by":     None,
                    "error_code":    None,
                    "error_message": None,
                    "completed_at":  now,
                    "updated_at":    now,
                }
            },
        )

        logger.info(
            "Job %s completed. Transcript %s (v%d, %d segments, %d words)",
            job_id, transcript_id, version,
            len(normalized["segments"]), len(normalized["words"]),
        )

    except Exception as exc:
        logger.exception("Unexpected error in job %s: %s", job_id, exc)
        await fail("INTERNAL_ERROR", str(exc))

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


# ── Recover stuck jobs on startup ────────────────────────────────────────────
async def recover_stuck_jobs(core_db) -> int:
    """
    On startup: reset any jobs stuck in 'processing' by a previous process
    back to 'queued' so they can be retried.
    """
    result = await core_db.transcription_jobs.update_many(
        {"status": "processing"},
        {
            "$set": {
                "status":    "queued",
                "locked_at": None,
                "locked_by": None,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    if result.modified_count:
        logger.info("Recovered %d stuck transcription jobs", result.modified_count)
    return result.modified_count


# ── Worker loop ───────────────────────────────────────────────────────────────
async def transcription_worker_loop():
    """
    Infinitely poll core_db.transcription_jobs.
    This is started as an asyncio Task from the FastAPI lifespan.
    """
    from database import get_core_db, get_project_db  # late import avoids circular

    # Wait for DB to be ready (brief delay on startup)
    await asyncio.sleep(3)

    core_db = get_core_db()
    if core_db is None:
        logger.error("Transcription worker: database not available, stopping.")
        return

    logger.info("Transcription worker %s started (poll=%ds)", WORKER_ID, WORKER_POLL_INTERVAL)

    await recover_stuck_jobs(core_db)

    while True:
        try:
            job = await claim_next_job(core_db)
            if job:
                await process_transcription_job(job, core_db, get_project_db)
            else:
                await asyncio.sleep(WORKER_POLL_INTERVAL)
        except asyncio.CancelledError:
            logger.info("Transcription worker %s shutting down.", WORKER_ID)
            break
        except Exception as exc:
            logger.exception("Worker loop unhandled error: %s", exc)
            await asyncio.sleep(5)
