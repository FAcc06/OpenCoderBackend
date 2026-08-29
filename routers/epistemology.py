"""
Epistemology API — behavior-based reflection (not an onboarding stance picker).

Endpoints:
  GET  /api/epistemology/{project_id}/status
  GET  /api/epistemology/{project_id}/history
  GET  /api/epistemology/{project_id}/inference
  POST /api/epistemology/{project_id}/reflection-response
  GET  /api/epistemology/{project_id}/coders
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from database import get_core_db
from routers.auth import verify_token
from services import epistemology_service as epi

router = APIRouter(prefix="/api/epistemology", tags=["epistemology"])


class ReflectionResponseBody(BaseModel):
    """Researcher confirmation / correction of an inferred reflection."""

    coder_user_id: Optional[str] = Field(
        None, description="Scope to this coder; omit for project-level aggregate"
    )
    response_type: str = Field(
        ...,
        description="confirm | correct | does_not_fit",
    )
    researcher_response: str = Field(
        ...,
        description="Stance label selected by researcher, or 'does_not_fit'",
    )
    system_inference: Optional[str] = None
    scores: Optional[Dict[str, int]] = None
    evidence_snapshot: Optional[List[str]] = None
    inference_version: Optional[int] = None
    note: Optional[str] = None
    mark_revelation_seen: bool = True


def _require_user(token: str) -> Dict[str, Any]:
    try:
        return verify_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


async def _assert_project_exists(project_id: str) -> ObjectId:
    try:
        oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project_id")
    core_db = get_core_db()
    proj = await core_db.projects.find_one({"_id": oid}, {"_id": 1})
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return oid


@router.get("/{project_id}/coders")
async def epistemology_coders(project_id: str, token: str = Query(...)):
    """List coders who have coding / memo activity (for per-coder Epistemology view)."""
    _require_user(token)
    await _assert_project_exists(project_id)
    return {"coders": await epi.list_project_coders(project_id)}


@router.get("/{project_id}/status")
async def epistemology_status(
    project_id: str,
    token: str = Query(...),
    coder_user_id: Optional[str] = Query(None),
):
    """
    Coded-segment progress + whether the first reflection is ready.
    Tracking/aggregation always run; revelationReady after ~20 coded segments.
    """
    _require_user(token)
    await _assert_project_exists(project_id)
    bundle = await epi.build_inference_bundle(project_id, coder_user_id)

    # Auto-mark threshold once when first crossed (does not auto-declare stance to UI)
    if bundle["revelationReady"] and not bundle["revelationTriggered"]:
        await epi.mark_revelation_triggered(project_id, coder_user_id, bundle)
        bundle["revelationTriggered"] = True

    return {
        "projectId": project_id,
        "coderUserId": coder_user_id,
        "codedSegmentCount": bundle["codedSegmentCount"],
        "revelationThreshold": bundle["revelationThreshold"],
        "revelationReady": bundle["revelationReady"],
        "revelationTriggered": bundle["revelationTriggered"],
        "inferredStance": bundle["inferredStance"] if bundle["revelationReady"] else None,
        "scores": bundle["scores"] if bundle["revelationReady"] else None,
        "evidence": bundle["evidence"] if bundle["revelationReady"] else [],
        "prompt": (
            "Based on recent coding practices, we observed patterns associated with "
            f"{bundle['inferredStance']} approaches. Does this interpretation fit how you "
            "understand your approach?"
            if bundle["revelationReady"]
            else None
        ),
        "disclaimer": (
            "This is an evidence-based reflection from observed practices, "
            "not a definitive classification of the researcher."
        ),
        "coverageNotes": bundle["coverageNotes"],
    }


@router.get("/{project_id}/inference")
async def epistemology_inference(
    project_id: str,
    token: str = Query(...),
    coder_user_id: Optional[str] = Query(None),
    force: bool = Query(
        False,
        description="If true, return inference even before the 20-segment threshold",
    ),
):
    _require_user(token)
    await _assert_project_exists(project_id)
    bundle = await epi.build_inference_bundle(project_id, coder_user_id)

    if not force and not bundle["revelationReady"]:
        return {
            "projectId": project_id,
            "coderUserId": coder_user_id,
            "codedSegmentCount": bundle["codedSegmentCount"],
            "revelationReady": False,
            "message": (
                f"Full reflection unlocks after approximately "
                f"{bundle['revelationThreshold']} coded segments "
                f"(currently {bundle['codedSegmentCount']}). "
                "Behavior tracking continues in the background."
            ),
            "scores": bundle["scores"],  # soft preview allowed for debugging managers
            "inferredStance": "emerging",
            "evidence": bundle["evidence"],
            "aggregation": bundle["aggregation"],
            "inferenceVersion": bundle["inferenceVersion"],
            "coverageNotes": bundle["coverageNotes"],
        }

    if bundle["revelationReady"] and not bundle["revelationTriggered"]:
        await epi.mark_revelation_triggered(project_id, coder_user_id, bundle)

    return {
        "projectId": project_id,
        "coderUserId": coder_user_id,
        "codedSegmentCount": bundle["codedSegmentCount"],
        "revelationReady": bundle["revelationReady"],
        "inferredStance": bundle["inferredStance"],
        "scores": bundle["scores"],
        "evidence": bundle["evidence"],
        "aggregation": bundle["aggregation"],
        "inferenceVersion": bundle["inferenceVersion"],
        "prompt": (
            "Based on your recent coding practices, we observed several patterns "
            f"associated with {bundle['inferredStance']} coding. "
            "Does this interpretation fit how you understand your approach?"
        ),
        "disclaimer": (
            "OpenCoder does not declare that you \"are\" this stance; "
            "you may confirm, correct, or reject the interpretation."
        ),
        "coverageNotes": bundle["coverageNotes"],
    }


@router.get("/{project_id}/history")
async def epistemology_history(
    project_id: str,
    token: str = Query(...),
    coder_user_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Research-decision history derived from existing OpenCoder activity + coding units."""
    _require_user(token)
    await _assert_project_exists(project_id)
    events = await epi.collect_research_events(project_id, coder_user_id, limit=limit)
    reflections = await epi.list_reflections(project_id, coder_user_id)
    return {
        "projectId": project_id,
        "coderUserId": coder_user_id,
        "events": events,
        "reflections": reflections,
        "coverageNotes": epi._coverage_notes(),
    }


@router.post("/{project_id}/reflection-response")
async def epistemology_reflection_response(
    project_id: str,
    body: ReflectionResponseBody,
    token: str = Query(...),
):
    """
    Store researcher confirmation/correction without overwriting systemInference.
    The response itself becomes part of reflexivity history.
    """
    user = _require_user(token)
    await _assert_project_exists(project_id)

    rt = (body.response_type or "").strip().lower()
    if rt not in ("confirm", "correct", "does_not_fit"):
        raise HTTPException(
            status_code=400,
            detail="response_type must be confirm | correct | does_not_fit",
        )

    # Prefer live bundle if client omitted snapshot fields
    bundle = await epi.build_inference_bundle(project_id, body.coder_user_id)
    system_inference = body.system_inference or bundle["inferredStance"]
    scores = body.scores or bundle["scores"]
    evidence = body.evidence_snapshot if body.evidence_snapshot is not None else bundle["evidence"]
    version = body.inference_version or bundle["inferenceVersion"]

    researcher_response = (body.researcher_response or "").strip()
    if rt == "does_not_fit":
        researcher_response = researcher_response or "does_not_fit"
    if not researcher_response:
        raise HTTPException(status_code=400, detail="researcher_response is required")

    responding_id = str(user.get("sub") or user.get("user_id") or user.get("id") or "")
    doc = await epi.save_reflection_response(
        project_id,
        coder_user_id=body.coder_user_id,
        responding_user_id=responding_id,
        system_inference=system_inference,
        researcher_response=researcher_response,
        response_type=rt,
        scores=scores,
        evidence_snapshot=evidence,
        inference_version=int(version),
        note=body.note,
    )

    if body.mark_revelation_seen:
        await epi.mark_revelation_triggered(project_id, body.coder_user_id, bundle)

    # Also append to activity log (reuses Project Logbook pipeline)
    try:
        from services.activity_log_service import log_user_activity

        core_db = get_core_db()
        uid = ObjectId(responding_id) if ObjectId.is_valid(responding_id) else None
        if uid:
            await log_user_activity(
                core_db,
                uid,
                "epistemology.reflection_response",
                f"Epistemology reflection: {rt} → {researcher_response}",
                project_id=ObjectId(project_id),
                event_type="epistemology.reflection_recorded",
                resource_type="epistemology",
                resource_id=doc.get("id"),
                role=user.get("role") or "project-manager",
                payload={
                    "systemInference": system_inference,
                    "researcherResponse": researcher_response,
                    "responseType": rt,
                    "coder_user_id": body.coder_user_id,
                    "inferenceVersion": version,
                },
            )
    except Exception:
        pass

    return {
        "success": True,
        "reflection": {
            "id": doc.get("id"),
            "systemInference": doc["systemInference"],
            "researcherResponse": doc["researcherResponse"],
            "responseType": doc["responseType"],
            "scores": doc["scores"],
            "evidenceSnapshot": doc["evidenceSnapshot"],
            "timestamp": doc["timestamp"].isoformat()
            if hasattr(doc["timestamp"], "isoformat")
            else doc["timestamp"],
            "inferenceVersion": doc["inferenceVersion"],
            "coder_user_id": doc.get("coder_user_id"),
            "note": doc.get("note"),
        },
    }
