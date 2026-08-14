"""
Diff tag groups and emit timeline activity logs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from bson import ObjectId


def _opt_id(opt: Any, index: int = 0) -> str:
    if isinstance(opt, dict):
        return str(opt.get("option_id") or opt.get("id") or opt.get("value") or index)
    return str(getattr(opt, "option_id", None) or getattr(opt, "id", None) or index)


def _opt_label(opt: Any) -> str:
    if isinstance(opt, dict):
        return str(opt.get("label") or opt.get("name") or "")
    return str(getattr(opt, "label", None) or getattr(opt, "name", None) or "")


def _opt_description(opt: Any) -> str:
    if isinstance(opt, dict):
        raw = opt.get("description")
    else:
        raw = getattr(opt, "description", None)
    if raw is None:
        return ""
    text = str(raw).strip()
    if text in ("", "None", "null", "undefined"):
        return ""
    return text


def snapshot_group(group: Any) -> Dict[str, Any]:
    """Normalize DB doc or Pydantic-ish group into timeline TagGroupSnapshot."""
    if hasattr(group, "dict"):
        g = group.dict()
    elif isinstance(group, dict):
        g = group
    else:
        g = {}

    options_raw = g.get("options") or []
    options = []
    for i, opt in enumerate(options_raw):
        if hasattr(opt, "dict"):
            opt = opt.dict()
        entry = {"id": _opt_id(opt, i), "label": _opt_label(opt)}
        desc = _opt_description(opt)
        if desc:
            entry["description"] = desc
        options.append(entry)

    gid = str(g.get("group_id") or g.get("id") or "")
    return {
        "id": gid,
        "name": str(g.get("name") or gid),
        "description": str(g.get("description") or ""),
        "options": options,
    }


def _options_map(snap: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for o in snap.get("options") or []:
        out[o["id"]] = {
            "label": o.get("label") or "",
            "description": o.get("description") or "",
        }
    return out


def diff_group(
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    """
    Returns (event_type, change_payload) for one group.
    event_type: tag.group_created | tag.group_updated | tag.group_deleted
    """
    if before is None and after is not None:
        return "tag.group_created", {
            "groupId": after["id"],
            "groupName": after["name"],
            "group": after,
            "addedOptions": list(after.get("options") or []),
        }

    if before is not None and after is None:
        return "tag.group_deleted", {
            "groupId": before["id"],
            "groupName": before["name"],
            "group": before,
            "removedOptions": list(before.get("options") or []),
        }

    assert before is not None and after is not None
    before_opts = _options_map(before)
    after_opts = _options_map(after)

    added = [
        {"id": i, "label": after_opts[i]["label"], **({"description": after_opts[i]["description"]} if after_opts[i]["description"] else {})}
        for i in after_opts if i not in before_opts
    ]
    removed = [
        {"id": i, "label": before_opts[i]["label"], **({"description": before_opts[i]["description"]} if before_opts[i]["description"] else {})}
        for i in before_opts if i not in after_opts
    ]
    renamed = [
        {"from": before_opts[i]["label"], "to": after_opts[i]["label"]}
        for i in before_opts
        if i in after_opts and before_opts[i]["label"] != after_opts[i]["label"]
    ]
    description_changed_options = [
        {
            "id": i,
            "label": after_opts[i]["label"],
            "from": before_opts[i]["description"],
            "to": after_opts[i]["description"],
        }
        for i in before_opts
        if i in after_opts and before_opts[i]["description"] != after_opts[i]["description"]
    ]
    name_changed = None
    if before["name"] != after["name"]:
        name_changed = {"from": before["name"], "to": after["name"]}

    group_description_changed = None
    if (before.get("description") or "") != (after.get("description") or ""):
        group_description_changed = {
            "from": before.get("description") or "",
            "to": after.get("description") or "",
        }

    change = {
        "groupId": after["id"],
        "groupName": after["name"],
        "before": before,
        "after": after,
        "addedOptions": added,
        "removedOptions": removed,
        "renamedOptions": renamed,
        "optionDescriptionChanged": description_changed_options,
    }
    if name_changed:
        change["nameChanged"] = name_changed
    if group_description_changed:
        change["descriptionChanged"] = group_description_changed

    return "tag.group_updated", change


def group_changed(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    return (
        before.get("name") != after.get("name")
        or (before.get("description") or "") != (after.get("description") or "")
        or _options_map(before) != _options_map(after)
    )


async def log_tag_overwrite_diff(
    core_db,
    *,
    actor_id: ObjectId,
    project_id: ObjectId,
    before_docs: List[Any],
    after_groups: List[Any],
    change_note: Optional[str] = None,
    change_group_id: Optional[str] = None,
) -> None:
    """Emit one activity log per created / updated / deleted group."""
    from services.activity_log_service import log_user_activity

    before_map = {s["id"]: s for s in (snapshot_group(g) for g in before_docs) if s["id"]}
    after_map = {s["id"]: s for s in (snapshot_group(g) for g in after_groups) if s["id"]}

    all_ids = set(before_map) | set(after_map)
    for gid in sorted(all_ids):
        b = before_map.get(gid)
        a = after_map.get(gid)
        if b and a and not group_changed(b, a):
            continue
        if b is None:
            event_type, change = diff_group(None, a)
            summary = f"Created tag group {change['groupName']}"
        elif a is None:
            event_type, change = diff_group(b, None)
            summary = f"Deleted tag group {change['groupName']}"
        else:
            event_type, change = diff_group(b, a)
            summary = f"Updated tag group {change['groupName']}"

        note = None
        if change_note:
            # Attach note to the focused group, or to every changed group if no focus
            if not change_group_id or change_group_id == gid:
                note = change_note
                change["changeNote"] = change_note

        payload: Dict[str, Any] = {"change": change}
        if note:
            payload["changeNote"] = note

        await log_user_activity(
            core_db,
            actor_id,
            event_type,
            summary,
            project_id=project_id,
            event_type=event_type,
            resource_type="tag_group",
            resource_id=gid,
            role="project-manager",
            payload=payload,
        )
