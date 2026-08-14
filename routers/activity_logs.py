"""
Activity logs API — Manager/Coder behavior audit trail.
"""
from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from bson import ObjectId
from datetime import datetime
import os

from database import get_core_db
from services.activity_log_service import (
    write_activity,
    list_activities,
    ensure_activity_indexes,
)
from services.timeline_service import build_project_timeline
from services.membership_service import is_project_manager, get_membership

router = APIRouter()


class ActivityCreate(BaseModel):
    action: str = Field(..., min_length=1, max_length=120)
    summary: str = Field(..., min_length=1, max_length=500)
    project_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    payload: Optional[Dict[str, Any]] = None
    event_type: Optional[str] = None


def _decode_user(token: str):
    from jose import jwt, JWTError
    try:
        SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM = os.getenv("ALGORITHM", "HS256")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return ObjectId(user_id), payload.get("email")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.post("/api/activity-logs")
async def create_activity_log(
    body: ActivityCreate = Body(...),
    token: str = Query(...),
):
    """Any authenticated user can append their own activity (fire-and-forget from FE)."""
    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    await ensure_activity_indexes(core_db)
    user_oid, email = _decode_user(token)
    user = await core_db.users.find_one({"_id": user_oid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    project_oid = None
    if body.project_id:
        try:
            project_oid = ObjectId(body.project_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid project_id")
        membership = await get_membership(core_db, user_oid, project_oid)
        # Also allow if active shell project matches
        if not membership and str(user.get("project_id")) != str(project_oid):
            raise HTTPException(status_code=403, detail="Not a member of this project")
    else:
        if user.get("project_id"):
            project_oid = user["project_id"]

    doc = await write_activity(
        core_db,
        user_id=user_oid,
        user_email=email or user.get("email"),
        user_name=user.get("name"),
        project_id=project_oid,
        role=user.get("role"),
        action=body.action.strip(),
        event_type=body.event_type,
        summary=body.summary.strip(),
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        meta=body.meta,
        payload=body.payload if body.payload is not None else body.meta,
    )
    return {"success": True, "id": str(doc["_id"])}


@router.get("/api/projects/{project_id}/timeline")
async def get_project_timeline(
    project_id: str,
    token: str = Query(...),
    days: int = Query(30, ge=1, le=90),
    cursor: Optional[str] = Query(None, description="Opaque cursor from previous next_cursor"),
    limit: int = Query(5000, ge=1, le=5000, description="Max raw logs per page"),
):
    """
    Manager-facing Project Activity Timeline (paginated).
    Each page scans up to `limit` raw logs (default 5000), maps + aggregates.
    Pass `cursor=next_cursor` to load older activity (Load more).
    """
    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    user_oid, _email = _decode_user(token)
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project_id")

    if not await is_project_manager(core_db, user_oid, project_oid):
        raise HTTPException(status_code=403, detail="Only managers can view the project timeline")

    await ensure_activity_indexes(core_db)
    return await build_project_timeline(
        core_db,
        project_id=project_oid,
        days=days,
        limit_raw=limit,
        cursor=cursor,
    )


@router.get("/api/projects/{project_id}/activity-logs")
async def get_project_activity_logs(
    project_id: str,
    token: str = Query(...),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    action: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
):
    """Managers (or project owners) can list activity for a project."""
    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    user_oid, _email = _decode_user(token)
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project_id")

    if not await is_project_manager(core_db, user_oid, project_oid):
        raise HTTPException(status_code=403, detail="Only managers can view activity logs")

    filter_user = None
    if user_id:
        try:
            filter_user = ObjectId(user_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid user_id")

    return await list_activities(
        core_db,
        project_id=project_oid,
        user_id=filter_user,
        action=action,
        page=page,
        limit=limit,
    )


@router.get("/api/activity-logs/mine")
async def get_my_activity_logs(
    token: str = Query(...),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """Current user can see their own recent activity."""
    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    user_oid, _ = _decode_user(token)
    return await list_activities(
        core_db,
        user_id=user_oid,
        page=page,
        limit=limit,
    )
