"""
Project Activity Timeline — project logs into TimelineEvents.

Raw activity_logs are filtered, mapped to event types, and aggregated
(e.g. many annotation.submit → one coding.activity per user per day).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from bson import ObjectId
import logging

logger = logging.getLogger(__name__)

# Max raw logs per page (memory guard). Not final feed card count —
# uploads/coding aggregate per user per day. Use cursor + Load more for the rest.
TIMELINE_PAGE_SIZE = 5000
TIMELINE_RAW_CAP = TIMELINE_PAGE_SIZE  # alias for callers

# Actions excluded from the product feed
TIMELINE_EXCLUDE_ACTIONS = frozenset({
    "project.enter",
})

# Map atomic log actions → timeline event type + renderer
ACTION_MAP: Dict[str, Tuple[str, str]] = {
    "project.created": ("project.created", "project"),
    "member.applied": ("member.applied", "member"),
    "application.apply": ("member.applied", "member"),
    "member.approved": ("member.approved", "member"),
    "application.approve": ("member.approved", "member"),
    "member.rejected": ("member.rejected", "member"),
    "application.reject": ("member.rejected", "member"),
    "member.assigned": ("member.assigned", "member"),
    "assignment.create": ("member.assigned", "member"),
    "file.uploaded": ("file.uploaded", "file"),
    "task.create": ("file.uploaded", "file"),
    "file.deleted": ("file.deleted", "file"),
    "task.delete": ("file.deleted", "file"),
    "tag.group_created": ("tag.group_created", "tag"),
    "tag.group_updated": ("tag.group_updated", "tag"),
    "tag.group_deleted": ("tag.group_deleted", "tag"),
    "annotation.submit": ("coding.activity", "coding"),
    "coding.activity": ("coding.activity", "coding"),
    "icr.resolved": ("icr.resolved", "icr"),
    "export.completed": ("export.completed", "export"),
    "transcription.completed": ("transcription.completed", "transcription"),
    "transcription.summarized": ("transcription.summarized", "transcription"),
}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


def _actor_from_log(doc: Dict[str, Any], user_cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    if doc.get("role") == "system" or not doc.get("user_id"):
        if doc.get("role") == "system" or (doc.get("user_name") or "").lower() == "system":
            return {
                "id": "system",
                "name": doc.get("user_name") or "System",
                "email": None,
                "avatarUrl": None,
                "role": "system",
            }
    uid = str(doc["user_id"]) if doc.get("user_id") else None
    cached = user_cache.get(uid or "") if uid else None
    return {
        "id": uid or "unknown",
        "name": (cached or {}).get("name") or doc.get("user_name") or "Unknown",
        "email": (cached or {}).get("email") or doc.get("user_email"),
        "avatarUrl": (cached or {}).get("avatar_url"),
        "role": doc.get("role") or (cached or {}).get("role"),
    }


def _payload(doc: Dict[str, Any]) -> Dict[str, Any]:
    return dict(doc.get("payload") or doc.get("meta") or {})


def _day_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


async def _load_users(core_db, user_ids: List[ObjectId]) -> Dict[str, Dict[str, Any]]:
    if not user_ids:
        return {}
    cursor = core_db.users.find(
        {"_id": {"$in": list(set(user_ids))}},
        {"name": 1, "email": 1, "avatar_url": 1, "role": 1},
    )
    out: Dict[str, Dict[str, Any]] = {}
    async for u in cursor:
        out[str(u["_id"])] = u
    return out


def _map_single(
    doc: Dict[str, Any],
    event_type: str,
    renderer: str,
    actor: Dict[str, Any],
    payload: Dict[str, Any],
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "type": event_type,
        "renderer": renderer,
        "createdAt": _iso(doc.get("created_at")),
        "actor": actor,
        "summary": summary or doc.get("summary") or event_type,
        "payload": payload,
    }


def encode_timeline_cursor(created_at: datetime, doc_id: ObjectId) -> str:
    return f"{_iso(created_at)}|{doc_id}"


def decode_timeline_cursor(cursor: Optional[str]) -> Tuple[Optional[datetime], Optional[ObjectId]]:
    if not cursor or "|" not in cursor:
        return None, None
    ts, oid = cursor.rsplit("|", 1)
    try:
        # Support ...Z and naive ISO
        cleaned = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)  # store/compare as naive UTC
        return dt, ObjectId(oid)
    except Exception:
        return None, None


def _aggregate_logs_to_events(
    logs: List[Dict[str, Any]],
    user_cache: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    coding_buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    file_upload_buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    file_delete_buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    passthrough: List[Dict[str, Any]] = []

    event_type_to_renderer = {et: r for et, r in ACTION_MAP.values()}

    for doc in logs:
        action = doc.get("action") or ""
        mapped = ACTION_MAP.get(action)
        if not mapped:
            et = doc.get("event_type")
            if et and et in event_type_to_renderer:
                event_type, renderer = et, event_type_to_renderer[et]
            else:
                continue
        else:
            event_type, renderer = mapped

        actor = _actor_from_log(doc, user_cache)
        created = doc.get("created_at") or datetime.utcnow()
        uid = actor["id"]
        dkey = _day_key(created)

        if event_type == "coding.activity":
            coding_buckets.setdefault((uid, dkey), []).append(doc)
        elif event_type == "file.uploaded":
            file_upload_buckets.setdefault((uid, dkey), []).append(doc)
        elif event_type == "file.deleted":
            file_delete_buckets.setdefault((uid, dkey), []).append(doc)
        else:
            payload = _enrich_passthrough_payload(event_type, doc, user_cache)
            passthrough.append(
                _map_single(doc, event_type, renderer, actor, payload)
            )

    events: List[Dict[str, Any]] = list(passthrough)

    for (uid, dkey), docs in coding_buckets.items():
        docs_sorted = sorted(docs, key=lambda d: d.get("created_at") or datetime.min, reverse=True)
        latest = docs_sorted[0]
        actor = _actor_from_log(latest, user_cache)
        tasks = []
        for d in docs_sorted:
            p = _payload(d)
            tasks.append({
                "taskId": p.get("task_id") or d.get("resource_id"),
                "title": p.get("task_title") or d.get("summary") or "Task",
                "taskType": p.get("task_type"),
                "completedAt": _iso(d.get("created_at")),
            })
        events.append({
            "id": f"coding:{uid}:{dkey}",
            "type": "coding.activity",
            "renderer": "coding",
            "createdAt": _iso(latest.get("created_at")),
            "actor": actor,
            "summary": f"Completed {len(tasks)} coding task{'s' if len(tasks) != 1 else ''}",
            "payload": {
                "completedCount": len(tasks),
                "tasks": tasks,
            },
        })

    for (uid, dkey), docs in file_upload_buckets.items():
        docs_sorted = sorted(docs, key=lambda d: d.get("created_at") or datetime.min, reverse=True)
        latest = docs_sorted[0]
        actor = _actor_from_log(latest, user_cache)
        files = []
        for d in docs_sorted:
            p = _payload(d)
            if p.get("files") and isinstance(p["files"], list):
                files.extend(p["files"])
            else:
                files.append({
                    "taskId": p.get("task_id") or d.get("resource_id"),
                    "title": p.get("title") or p.get("filename") or d.get("summary") or "File",
                    "mediaType": p.get("media_type") or p.get("task_type"),
                })
        events.append({
            "id": f"fileup:{uid}:{dkey}",
            "type": "file.uploaded",
            "renderer": "file",
            "createdAt": _iso(latest.get("created_at")),
            "actor": actor,
            "summary": f"Uploaded {len(files)} task{'s' if len(files) != 1 else ''}",
            "payload": {"count": len(files), "files": files},
        })

    for (uid, dkey), docs in file_delete_buckets.items():
        docs_sorted = sorted(docs, key=lambda d: d.get("created_at") or datetime.min, reverse=True)
        latest = docs_sorted[0]
        actor = _actor_from_log(latest, user_cache)
        files = []
        for d in docs_sorted:
            p = _payload(d)
            files.append({
                "taskId": p.get("task_id") or d.get("resource_id"),
                "title": p.get("title") or d.get("summary") or "Task",
                "mediaType": p.get("media_type") or p.get("task_type"),
            })
        events.append({
            "id": f"filedel:{uid}:{dkey}",
            "type": "file.deleted",
            "renderer": "file",
            "createdAt": _iso(latest.get("created_at")),
            "actor": actor,
            "summary": f"Deleted {len(files)} task{'s' if len(files) != 1 else ''}",
            "payload": {"count": len(files), "files": files},
        })

    events.sort(key=lambda e: e.get("createdAt") or "", reverse=True)
    return events


async def build_project_timeline(
    core_db,
    *,
    project_id: ObjectId,
    days: int = 30,
    limit_raw: int = TIMELINE_PAGE_SIZE,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """
    One page of timeline: up to `limit_raw` atomic logs → mapped/aggregated events.
    Pass `cursor` from the previous response's `next_cursor` to load older activity.
    """
    days = 7 if days <= 7 else 30
    cutoff = datetime.utcnow() - timedelta(days=days)
    page_size = max(1, min(limit_raw, TIMELINE_PAGE_SIZE))

    query: Dict[str, Any] = {
        "project_id": project_id,
        "action": {"$nin": list(TIMELINE_EXCLUDE_ACTIONS)},
    }

    before_dt, before_id = decode_timeline_cursor(cursor)
    if before_dt is not None and before_id is not None:
        # Strictly older than cursor (created_at, _id) for stable paging
        query["$and"] = [
            {"created_at": {"$gte": cutoff}},
            {
                "$or": [
                    {"created_at": {"$lt": before_dt}},
                    {"created_at": before_dt, "_id": {"$lt": before_id}},
                ]
            },
        ]
    else:
        query["created_at"] = {"$gte": cutoff}

    mongo_cursor = (
        core_db.activity_logs.find(query)
        .sort([("created_at", -1), ("_id", -1)])
        .limit(page_size + 1)
    )
    fetched: List[Dict[str, Any]] = [d async for d in mongo_cursor]
    has_more = len(fetched) > page_size
    logs = fetched[:page_size]

    next_cursor = None
    if has_more and logs:
        oldest = logs[-1]
        next_cursor = encode_timeline_cursor(oldest["created_at"], oldest["_id"])

    user_oids: List[ObjectId] = [d["user_id"] for d in logs if d.get("user_id")]
    for d in logs:
        p = _payload(d)
        for key in ("applicant_user_id", "coder_user_id", "assignee_user_id"):
            raw = p.get(key)
            if raw:
                try:
                    user_oids.append(ObjectId(str(raw)))
                except Exception:
                    pass
    user_cache = await _load_users(core_db, user_oids)
    events = _aggregate_logs_to_events(logs, user_cache)

    return {
        "days": days,
        "events": events,
        "total": len(events),
        "raw_scanned": len(logs),
        "page_size": page_size,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


def _enrich_passthrough_payload(
    event_type: str,
    doc: Dict[str, Any],
    user_cache: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    p = _payload(doc)

    if event_type in ("member.approved", "member.rejected"):
        applicant_id = p.get("applicant_user_id")
        if applicant_id and "applicant" not in p:
            u = user_cache.get(str(applicant_id))
            if not u and p.get("applicant_name"):
                p["applicant"] = {
                    "id": str(applicant_id),
                    "name": p.get("applicant_name"),
                    "email": p.get("applicant_email"),
                    "role": "coder",
                }
            elif u:
                p["applicant"] = {
                    "id": str(applicant_id),
                    "name": u.get("name"),
                    "email": u.get("email"),
                    "avatarUrl": u.get("avatar_url"),
                    "role": "coder",
                }
        if "applicant" not in p and p.get("applicant_name"):
            p["applicant"] = {
                "id": str(p.get("applicant_user_id") or "unknown"),
                "name": p.get("applicant_name"),
                "email": p.get("applicant_email"),
                "role": "coder",
            }

    if event_type == "member.assigned":
        assignee_id = p.get("coder_user_id") or p.get("assignee_user_id")
        if assignee_id and "assignee" not in p:
            u = user_cache.get(str(assignee_id))
            p["assignee"] = {
                "id": str(assignee_id),
                "name": (u or {}).get("name") or p.get("coder_name") or "Coder",
                "email": (u or {}).get("email"),
                "avatarUrl": (u or {}).get("avatar_url"),
                "role": "coder",
            }
        if "taskCount" not in p:
            p["taskCount"] = p.get("task_count") or len(p.get("task_ids") or p.get("taskTitles") or [])

    if event_type == "project.created":
        if "projectName" not in p and p.get("project_name"):
            p["projectName"] = p["project_name"]
        if "memo" not in p and p.get("project_memo"):
            p["memo"] = p["project_memo"]

    if event_type in ("tag.group_created", "tag.group_updated", "tag.group_deleted"):
        if "change" not in p and p:
            p = {"change": p}

    return p
