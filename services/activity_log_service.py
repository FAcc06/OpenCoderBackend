"""
Activity / audit logs for Manager and Coder actions.
Stored in core_db.activity_logs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from bson import ObjectId
import logging

logger = logging.getLogger(__name__)


async def ensure_activity_indexes(core_db) -> None:
    try:
        await core_db.activity_logs.create_index([("project_id", 1), ("created_at", -1)])
        await core_db.activity_logs.create_index(
            [("project_id", 1), ("created_at", -1), ("_id", -1)]
        )
        await core_db.activity_logs.create_index([("user_id", 1), ("created_at", -1)])
        await core_db.activity_logs.create_index([("action", 1), ("created_at", -1)])
    except Exception:
        pass


async def write_activity(
    core_db,
    *,
    user_id: Optional[ObjectId] = None,
    action: str,
    summary: str,
    project_id: Optional[ObjectId] = None,
    role: Optional[str] = None,
    user_email: Optional[str] = None,
    user_name: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    event_type: Optional[str] = None,
) -> Dict[str, Any]:
    structured = payload if payload is not None else (meta or {})
    doc = {
        "user_id": user_id,
        "user_email": user_email,
        "user_name": user_name,
        "project_id": project_id,
        "role": role,
        "action": action,
        "event_type": event_type,
        "summary": summary,
        "resource_type": resource_type,
        "resource_id": resource_id,
        # Keep both for backward compatibility; timeline prefers payload
        "payload": structured,
        "meta": structured,
        "created_at": datetime.utcnow(),
    }
    result = await core_db.activity_logs.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def log_user_activity(
    core_db,
    user_id: ObjectId,
    action: str,
    summary: str,
    *,
    project_id: Optional[ObjectId] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    event_type: Optional[str] = None,
    role: Optional[str] = None,
) -> None:
    """Best-effort server-side log (never raises to caller)."""
    try:
        user = await core_db.users.find_one({"_id": user_id})
        await write_activity(
            core_db,
            user_id=user_id,
            user_email=(user or {}).get("email"),
            user_name=(user or {}).get("name"),
            project_id=project_id or (user or {}).get("project_id"),
            role=role or (user or {}).get("role"),
            action=action,
            event_type=event_type,
            summary=summary,
            resource_type=resource_type,
            resource_id=resource_id,
            meta=meta,
            payload=payload if payload is not None else meta,
        )
    except Exception as e:
        logger.warning("activity log failed [%s]: %s", action, e)


async def log_system_activity(
    core_db,
    action: str,
    summary: str,
    *,
    project_id: Optional[ObjectId] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    event_type: Optional[str] = None,
) -> None:
    """Best-effort system/automation log (no human actor). Never raises."""
    try:
        await write_activity(
            core_db,
            user_id=None,
            user_name="System",
            role="system",
            project_id=project_id,
            action=action,
            event_type=event_type or action,
            summary=summary,
            resource_type=resource_type,
            resource_id=resource_id,
            meta=meta,
            payload=payload if payload is not None else meta,
        )
    except Exception as e:
        logger.warning("system activity log failed [%s]: %s", action, e)


def serialize_activity(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "user_id": str(doc["user_id"]) if doc.get("user_id") else None,
        "user_email": doc.get("user_email"),
        "user_name": doc.get("user_name"),
        "project_id": str(doc["project_id"]) if doc.get("project_id") else None,
        "role": doc.get("role"),
        "action": doc.get("action"),
        "event_type": doc.get("event_type"),
        "summary": doc.get("summary"),
        "resource_type": doc.get("resource_type"),
        "resource_id": doc.get("resource_id"),
        "meta": doc.get("meta") or {},
        "payload": doc.get("payload") or doc.get("meta") or {},
        "created_at": doc["created_at"].isoformat() + "Z" if doc.get("created_at") else None,
    }


async def list_activities(
    core_db,
    *,
    project_id: Optional[ObjectId] = None,
    user_id: Optional[ObjectId] = None,
    action: Optional[str] = None,
    since: Optional[datetime] = None,
    page: int = 1,
    limit: int = 50,
) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if project_id:
        query["project_id"] = project_id
    if user_id:
        query["user_id"] = user_id
    if action:
        query["action"] = action
    if since is not None:
        query["created_at"] = {"$gte": since}

    total = await core_db.activity_logs.count_documents(query)
    skip = max(0, (page - 1) * limit)
    cursor = (
        core_db.activity_logs.find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )
    items = [serialize_activity(d) async for d in cursor]
    pages = (total + limit - 1) // limit if limit else 1
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, pages),
    }


def try_user_id_from_token(token: Optional[str]) -> Optional[ObjectId]:
    """Best-effort JWT sub → ObjectId; returns None if missing/invalid."""
    if not token:
        return None
    try:
        from jose import jwt, JWTError
        import os
        SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM = os.getenv("ALGORITHM", "HS256")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if not sub:
            return None
        return ObjectId(str(sub))
    except Exception:
        return None
