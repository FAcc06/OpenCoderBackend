"""
Project membership helpers.

A user may belong to at most MAX_ACTIVE_MEMBERSHIPS projects
(create + join combined). One membership per (user, project) may
hold multiple roles: roles = ["manager", "coder"].
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any
from bson import ObjectId
from fastapi import HTTPException

MAX_ACTIVE_MEMBERSHIPS = 10
VALID_ROLES = ("manager", "coder")


def normalize_roles(doc: Optional[Dict[str, Any]]) -> List[str]:
    """Read roles[] from a membership doc; fall back to legacy single `role`."""
    if not doc:
        return []
    roles = doc.get("roles")
    if isinstance(roles, list) and roles:
        out = [r for r in roles if r in VALID_ROLES]
        # de-dupe, manager first for display
        seen = []
        for r in ("manager", "coder"):
            if r in out and r not in seen:
                seen.append(r)
        for r in out:
            if r not in seen:
                seen.append(r)
        return seen
    single = doc.get("role")
    if single in VALID_ROLES:
        return [single]
    return ["coder"]


def primary_role(roles: List[str]) -> str:
    """Preferred shell when as_role is omitted: manager if present else coder."""
    if "manager" in roles:
        return "manager"
    if "coder" in roles:
        return "coder"
    return "coder"


async def count_active_memberships(core_db, user_id: ObjectId) -> int:
    return await core_db.project_memberships.count_documents({
        "user_id": user_id,
        "status": "active",
    })


async def ensure_under_limit(core_db, user_id: ObjectId, *, extra: int = 1) -> None:
    """Raise 400 if adding `extra` memberships would exceed the limit."""
    n = await count_active_memberships(core_db, user_id)
    if n + extra > MAX_ACTIVE_MEMBERSHIPS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project limit reached ({MAX_ACTIVE_MEMBERSHIPS}). "
                "Leave or archive a project before joining or creating another."
            ),
        )


async def get_membership(
    core_db,
    user_id: ObjectId,
    project_id: ObjectId,
) -> Optional[Dict[str, Any]]:
    return await core_db.project_memberships.find_one({
        "user_id": user_id,
        "project_id": project_id,
        "status": "active",
    })


async def upsert_membership(
    core_db,
    *,
    user_id: ObjectId,
    project_id: ObjectId,
    role: str,
    status: str = "active",
    roles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Create or update a membership.

    - If `roles` is provided, use that list (normalized).
    - Otherwise ADD `role` into existing roles (never removes other roles).
    Also keeps legacy `role` field = primary_role for older readers.
    """
    if role not in VALID_ROLES and not roles:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")

    now = datetime.utcnow()
    existing = await core_db.project_memberships.find_one({
        "user_id": user_id,
        "project_id": project_id,
    })

    if roles is not None:
        final_roles = normalize_roles({"roles": roles})
    else:
        current = normalize_roles(existing)
        if role not in current:
            current.append(role)
        final_roles = normalize_roles({"roles": current})

    if not final_roles:
        final_roles = ["coder"]

    prim = primary_role(final_roles)

    await core_db.project_memberships.update_one(
        {"user_id": user_id, "project_id": project_id},
        {
            "$set": {
                "roles": final_roles,
                "role": prim,  # legacy single-role field
                "status": status,
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )
    return await core_db.project_memberships.find_one({
        "user_id": user_id,
        "project_id": project_id,
    })


async def bootstrap_legacy_memberships(core_db, user_id: ObjectId) -> None:
    """
    Backfill memberships for users who only have the legacy
    user.project_id / projects.owner_user_id fields.

    Owners and anyone with manager on a project also get coder so Hub
    can show Enter as Manager + Enter as Coder.
    """
    uid_str = str(user_id)

    # 1) Project owners → always manager + coder
    #    Match ObjectId or string owner_user_id (legacy data).
    async for proj in core_db.projects.find({
        "$or": [
            {"owner_user_id": user_id},
            {"owner_user_id": uid_str},
        ]
    }):
        existing = await get_membership(core_db, user_id, proj["_id"])
        roles = normalize_roles(existing)
        if "manager" not in roles or "coder" not in roles:
            await upsert_membership(
                core_db,
                user_id=user_id,
                project_id=proj["_id"],
                role="manager",
                roles=["manager", "coder"],
            )

    # 2) Any active membership that already has manager → add coder
    #    Covers rows created before dual-role existed (role/roles = manager only).
    async for m in core_db.project_memberships.find({
        "user_id": user_id,
        "status": "active",
    }):
        roles = normalize_roles(m)
        if "manager" in roles and "coder" not in roles:
            await upsert_membership(
                core_db,
                user_id=user_id,
                project_id=m["project_id"],
                role="manager",
                roles=["manager", "coder"],
            )

    user = await core_db.users.find_one({"_id": user_id})
    if not user:
        return
    pid = user.get("project_id")
    if not pid:
        return
    existing = await get_membership(core_db, user_id, pid)
    if existing:
        return
    owned = await core_db.projects.find_one({
        "_id": pid,
        "$or": [
            {"owner_user_id": user_id},
            {"owner_user_id": uid_str},
        ],
    })
    if owned:
        await upsert_membership(
            core_db,
            user_id=user_id,
            project_id=pid,
            role="manager",
            roles=["manager", "coder"],
        )
    else:
        # Preserve legacy shell role: managers who aren't owners still get both
        if user.get("role") == "manager":
            await upsert_membership(
                core_db,
                user_id=user_id,
                project_id=pid,
                role="manager",
                roles=["manager", "coder"],
            )
        else:
            await upsert_membership(
                core_db,
                user_id=user_id,
                project_id=pid,
                role="coder",
            )


async def list_user_projects(core_db, user_id: ObjectId) -> List[Dict[str, Any]]:
    await bootstrap_legacy_memberships(core_db, user_id)

    cursor = core_db.project_memberships.find({
        "user_id": user_id,
        "status": "active",
    }).sort("updated_at", -1)
    memberships = await cursor.to_list(length=100)

    result = []
    for m in memberships:
        proj = await core_db.projects.find_one({"_id": m["project_id"]})
        if not proj:
            continue
        roles = normalize_roles(m)
        result.append({
            "membership_id": str(m["_id"]),
            "project_id": str(m["project_id"]),
            "role": primary_role(roles),  # legacy
            "roles": roles,
            "status": m.get("status", "active"),
            "project_name": proj.get("name", "Untitled"),
            "project_slug": proj.get("slug", ""),
            "project_status": proj.get("status", "active"),
            "joined_at": m.get("created_at").isoformat() if m.get("created_at") else None,
            "updated_at": m.get("updated_at").isoformat() if m.get("updated_at") else None,
        })
    return result


async def user_has_membership_role(
    core_db,
    user_id: ObjectId,
    project_id: ObjectId,
    role: str,
) -> bool:
    membership = await get_membership(core_db, user_id, project_id)
    return role in normalize_roles(membership)


async def is_project_manager(core_db, user_id: ObjectId, project_id: ObjectId) -> bool:
    """True if user is project owner or has manager in membership roles."""
    uid_str = str(user_id)
    project = await core_db.projects.find_one({"_id": project_id})
    if project:
        owner = project.get("owner_user_id")
        if owner is not None and str(owner) == uid_str:
            return True
    return await user_has_membership_role(core_db, user_id, project_id, "manager")


async def get_project_manager_ids(core_db, project_id: ObjectId) -> List[ObjectId]:
    """Owner + users with manager membership (deduped)."""
    ids: List[ObjectId] = []
    seen = set()
    project = await core_db.projects.find_one({"_id": project_id})
    if project and project.get("owner_user_id"):
        oid = project["owner_user_id"]
        if not isinstance(oid, ObjectId):
            try:
                oid = ObjectId(str(oid))
            except Exception:
                oid = None
        if oid is not None:
            ids.append(oid)
            seen.add(str(oid))

    cursor = core_db.project_memberships.find({
        "project_id": project_id,
        "status": "active",
    })
    async for m in cursor:
        roles = normalize_roles(m)
        if "manager" not in roles:
            continue
        uid = m.get("user_id")
        key = str(uid)
        if key not in seen:
            ids.append(uid)
            seen.add(key)
    return ids


async def list_project_member_users(
    core_db,
    project_id: ObjectId,
    *,
    require_role: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Active project members from memberships, joined to users.
    If require_role is set (manager|coder), only members with that role.
    Each item: user fields + roles[] + role (primary).
    """
    cursor = core_db.project_memberships.find({
        "project_id": project_id,
        "status": "active",
    })
    memberships = await cursor.to_list(length=500)

    result: List[Dict[str, Any]] = []
    for m in memberships:
        roles = normalize_roles(m)
        if require_role and require_role not in roles:
            continue
        user = await core_db.users.find_one({"_id": m["user_id"]})
        if not user:
            continue
        result.append({
            "id": str(user["_id"]),
            "email": user.get("email"),
            "name": user.get("name"),
            "role": primary_role(roles),
            "roles": roles,
            "avatar_url": user.get("avatar_url"),
            "project_id": str(project_id),
            "active_shell_role": user.get("role"),
            "created_at": user.get("created_at"),
            "updated_at": user.get("updated_at"),
        })
    return result
