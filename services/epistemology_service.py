"""
Epistemology behavior tracking — aggregation + inference.

Reuses existing OpenCoder sources (activity_logs, annotations, transcript_codings,
passage_annotations, task_notes, consensus) rather than a duplicate memo system.

Inference rules live in config/epistemology_rules.json (versioned, swappable).
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from database import get_core_db, get_project_db

RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "epistemology_rules.json"
_RULES_CACHE: Optional[Dict[str, Any]] = None


def load_rules(force: bool = False) -> Dict[str, Any]:
    global _RULES_CACHE
    if _RULES_CACHE is not None and not force:
        return _RULES_CACHE
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        _RULES_CACHE = json.load(f)
    return _RULES_CACHE


def _clamp(n: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(n))))


def _oid(v: Any) -> Optional[ObjectId]:
    if isinstance(v, ObjectId):
        return v
    try:
        return ObjectId(str(v))
    except Exception:
        return None


def _iso_ts(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat() + ("Z" if v.tzinfo is None else "")
    return str(v)


def _uid_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    return str(v)


# ── Coding units (segments) ──────────────────────────────────────────────────


async def collect_coding_units(
    project_id: str,
    coder_user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    One 'coded segment' = one coding decision unit:
    - transcript_codings document
    - passage_annotations document
    - non-consensus task-level annotation (whole-task / text)
    """
    project_db = await get_project_db(project_id)
    if project_db is None:
        return []

    coder_oid = _oid(coder_user_id) if coder_user_id else None
    units: List[Dict[str, Any]] = []

    # Transcript segment/word codings
    tq: Dict[str, Any] = {}
    if coder_oid:
        tq["coder_user_id"] = coder_oid
    async for doc in project_db.transcript_codings.find(tq):
        snap = (doc.get("text_snapshot") or "").strip()
        span_len = len(snap) if snap else None
        target = doc.get("target") or {}
        units.append(
            {
                "kind": "transcript_coding",
                "unit_id": str(doc.get("_id")),
                "coder_user_id": _uid_str(doc.get("coder_user_id")),
                "task_id": _uid_str(doc.get("task_id")),
                "segment_id": _uid_str(target.get("transcript_id")),
                "span_length": span_len,
                "timestamp": doc.get("created_at") or doc.get("updated_at"),
                "metadata": {
                    "target_type": target.get("type"),
                    "label_count": len(doc.get("labels") or []),
                    "has_notes": bool((doc.get("notes") or "").strip()),
                },
            }
        )

    # PDF passage annotations
    pq: Dict[str, Any] = {}
    if coder_oid:
        pq["coder_user_id"] = coder_oid
    async for doc in project_db.passage_annotations.find(pq):
        selected = (doc.get("selected_text") or "").strip()
        start = doc.get("start_offset")
        end = doc.get("end_offset")
        span_len = None
        if selected:
            span_len = len(selected)
        elif isinstance(start, int) and isinstance(end, int) and end >= start:
            span_len = end - start
        units.append(
            {
                "kind": "passage_annotation",
                "unit_id": str(doc.get("_id")),
                "coder_user_id": _uid_str(doc.get("coder_user_id")),
                "task_id": _uid_str(doc.get("task_id")),
                "segment_id": str(doc.get("_id")),
                "span_length": span_len,
                "timestamp": doc.get("created_at") or doc.get("updated_at"),
                "metadata": {
                    "page_number": doc.get("page_number"),
                    "code_count": len(doc.get("code_ids") or []),
                    "has_note": bool((doc.get("note") or "").strip()),
                },
            }
        )

    # Task-level annotations (exclude consensus resolutions)
    aq: Dict[str, Any] = {"is_consensus": {"$ne": True}}
    if coder_oid:
        aq["coder_user_id"] = coder_oid
    async for doc in project_db.annotations.find(aq):
        units.append(
            {
                "kind": "task_annotation",
                "unit_id": str(doc.get("_id")),
                "coder_user_id": _uid_str(doc.get("coder_user_id")),
                "task_id": _uid_str(doc.get("task_id")),
                "segment_id": None,
                "span_length": None,  # whole-task; no char span stored
                "timestamp": doc.get("completed_at") or doc.get("created_at"),
                "metadata": {
                    "label_count": len(doc.get("labels") or []),
                    "has_notes": bool((doc.get("notes") or "").strip()),
                    "task_level": True,
                },
            }
        )

    return units


# ── Research-decision history (from existing logs + derived) ─────────────────


async def collect_research_events(
    project_id: str,
    coder_user_id: Optional[str] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """
    Build a research-decision oriented event list from activity_logs + coding units.
    Does not invent a parallel memo store.
    """
    core_db = get_core_db()
    project_oid = _oid(project_id)
    if not project_oid:
        return []

    q: Dict[str, Any] = {"project_id": project_oid}
    if coder_user_id:
        q["user_id"] = _oid(coder_user_id)

    events: List[Dict[str, Any]] = []
    cursor = (
        core_db.activity_logs.find(q)
        .sort([("created_at", -1)])
        .limit(limit)
    )
    async for doc in cursor:
        action = doc.get("action") or ""
        et = doc.get("event_type") or action
        payload = doc.get("payload") or doc.get("meta") or {}
        epi_type = _map_activity_to_epistemology(et, action, payload)
        events.append(
            {
                "projectId": project_id,
                "userId": _uid_str(doc.get("user_id")),
                "userName": doc.get("user_name"),
                "taskId": payload.get("task_id"),
                "segmentId": payload.get("segment_id"),
                "eventType": epi_type,
                "sourceEventType": et,
                "sourceAction": action,
                "timestamp": _iso_ts(doc.get("created_at")),
                "summary": doc.get("summary"),
                "metadata": payload,
            }
        )

    # Derive segment_coded events from coding units (richer than activity_logs alone)
    units = await collect_coding_units(project_id, coder_user_id)
    for u in units:
        events.append(
            {
                "projectId": project_id,
                "userId": u.get("coder_user_id"),
                "taskId": u.get("task_id"),
                "segmentId": u.get("segment_id") or u.get("unit_id"),
                "eventType": "segment_coded",
                "sourceEventType": u.get("kind"),
                "timestamp": _iso_ts(u.get("timestamp")),
                "metadata": {
                    "kind": u.get("kind"),
                    "span_length": u.get("span_length"),
                    **(u.get("metadata") or {}),
                },
            }
        )

    # Task memos → reflexive_note (content not duplicated; pointer only)
    project_db = await get_project_db(project_id)
    if project_db is not None:
        nq: Dict[str, Any] = {}
        if coder_user_id:
            nq["coder_user_id"] = _oid(coder_user_id)
        async for note in project_db.task_notes.find(nq).sort([("updated_at", -1)]).limit(200):
            content = (note.get("content") or "").strip()
            if not content:
                continue
            events.append(
                {
                    "projectId": project_id,
                    "userId": _uid_str(note.get("coder_user_id")),
                    "taskId": _uid_str(note.get("task_id")),
                    "segmentId": None,
                    "eventType": "reflexive_note",
                    "sourceEventType": "memo.added",
                    "timestamp": _iso_ts(note.get("updated_at") or note.get("created_at")),
                    "metadata": {
                        "note_id": str(note.get("_id")),
                        "content_length": len(content),
                    },
                }
            )

    # Disagreement open/resolved (derived from annotations)
    disagreement_events = await _disagreement_events(project_id, coder_user_id)
    events.extend(disagreement_events)

    # Sort newest first
    def _key(e: Dict[str, Any]):
        t = e.get("timestamp") or ""
        return t

    events.sort(key=_key, reverse=True)
    return events[: limit + 200]


def _map_activity_to_epistemology(event_type: str, action: str, payload: Dict) -> str:
    et = (event_type or "").lower()
    change = payload.get("change") if isinstance(payload.get("change"), dict) else {}

    if et in ("tag.group_created",) or action == "tag.group_created":
        return "code_creation"
    if et in ("tag.group_deleted",):
        return "code_deletion"
    if et in ("tag.group_updated",):
        if change.get("renamedOptions"):
            return "code_rename"
        if change.get("optionDescriptionChanged") or change.get("descriptionChanged"):
            return "code_definition_modification"
        if change.get("changeNote") or payload.get("change_note"):
            return "codebook_change_reason"
        return "codebook_change"
    if et in ("memo.added",):
        return "reflexive_note"
    if et in ("coding.activity",) or action == "annotation.submit":
        return "segment_coded"
    if et in ("icr.resolved",):
        return "disagreement_resolved"
    if et in ("epistemology.reflection_recorded",):
        return "epistemology_reflection"
    return et or action or "research_activity"


async def _disagreement_events(
    project_id: str,
    coder_user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Derive disagreement created/resolved/unresolved from annotation sets."""
    project_db = await get_project_db(project_id)
    if project_db is None:
        return []

    # Group non-consensus annotations by task
    pipeline = [
        {"$match": {"is_consensus": {"$ne": True}}},
        {
            "$group": {
                "_id": "$task_id",
                "coders": {"$addToSet": "$coder_user_id"},
                "count": {"$sum": 1},
                "anns": {"$push": {"coder": "$coder_user_id", "labels": "$labels"}},
            }
        },
    ]
    events: List[Dict[str, Any]] = []
    async for row in project_db.annotations.aggregate(pipeline):
        task_id = _uid_str(row.get("_id"))
        anns = row.get("anns") or []
        if len(anns) < 2:
            continue
        # Simple disagreement: label sets differ across coders
        sigs = []
        for a in anns:
            labels = a.get("labels") or []
            parts = []
            for lab in labels:
                gid = lab.get("group_id")
                opts = tuple(sorted(str(x) for x in (lab.get("option_ids") or [])))
                parts.append((gid, opts))
            sigs.append(tuple(sorted(parts)))
        if len(set(sigs)) <= 1:
            continue

        coder_filter = None
        if coder_user_id:
            coder_filter = _oid(coder_user_id)
            coder_ids = {_uid_str(c) for c in (row.get("coders") or [])}
            if coder_user_id not in coder_ids:
                continue

        consensus = await project_db.annotations.find_one(
            {"task_id": row["_id"], "is_consensus": True}
        )
        if consensus:
            events.append(
                {
                    "projectId": project_id,
                    "userId": _uid_str(consensus.get("coder_user_id")),
                    "taskId": task_id,
                    "segmentId": None,
                    "eventType": "disagreement_resolved",
                    "timestamp": _iso_ts(
                        consensus.get("completed_at") or consensus.get("created_at")
                    ),
                    "metadata": {"coder_count": len(row.get("coders") or [])},
                }
            )
        else:
            now = _iso_ts(datetime.utcnow())
            events.append(
                {
                    "projectId": project_id,
                    "userId": coder_user_id,
                    "taskId": task_id,
                    "segmentId": None,
                    "eventType": "disagreement_unresolved",
                    "timestamp": now,
                    "metadata": {
                        "coder_count": len(row.get("coders") or []),
                        "status": "open",
                    },
                }
            )
            events.append(
                {
                    "projectId": project_id,
                    "userId": coder_user_id,
                    "taskId": task_id,
                    "segmentId": None,
                    "eventType": "disagreement_created",
                    "timestamp": now,
                    "metadata": {"coder_count": len(row.get("coders") or [])},
                }
            )
    return events


# ── Aggregation ──────────────────────────────────────────────────────────────


def aggregate_behavior(
    events: List[Dict[str, Any]],
    units: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Modular behavioral aggregation (thresholds may change via rules file)."""
    renames = [e for e in events if e.get("eventType") == "code_rename"]
    creations = [e for e in events if e.get("eventType") == "code_creation"]
    deletions = [e for e in events if e.get("eventType") == "code_deletion"]
    def_mods = [e for e in events if e.get("eventType") == "code_definition_modification"]
    codebook_changes = [
        e
        for e in events
        if e.get("eventType")
        in (
            "code_rename",
            "code_creation",
            "code_deletion",
            "code_definition_modification",
            "codebook_change",
            "codebook_change_reason",
        )
    ]
    with_reason = 0
    for e in codebook_changes:
        meta = e.get("metadata") or {}
        change = meta.get("change") if isinstance(meta.get("change"), dict) else {}
        note = (
            change.get("changeNote")
            or meta.get("change_note")
            or meta.get("changeNote")
            or ""
        )
        if str(note).strip():
            with_reason += 1

    rename_with_reason = 0
    for e in renames:
        meta = e.get("metadata") or {}
        change = meta.get("change") if isinstance(meta.get("change"), dict) else {}
        if str(change.get("changeNote") or meta.get("change_note") or "").strip():
            rename_with_reason += 1

    unresolved = sum(1 for e in events if e.get("eventType") == "disagreement_unresolved")
    resolved = sum(1 for e in events if e.get("eventType") == "disagreement_resolved")
    memos = sum(1 for e in events if e.get("eventType") == "reflexive_note")
    reflections = sum(1 for e in events if e.get("eventType") == "epistemology_reflection")

    spans = [u["span_length"] for u in units if isinstance(u.get("span_length"), int) and u["span_length"] > 0]
    span_cv = 0.0
    if len(spans) >= 2:
        mean = statistics.mean(spans)
        if mean > 0:
            span_cv = statistics.pstdev(spans) / mean
    elif len(spans) == 1:
        span_cv = 0.0

    segment_like = sum(1 for u in units if u.get("kind") in ("transcript_coding", "passage_annotation"))
    whole_like = sum(1 for u in units if u.get("kind") == "task_annotation")
    total_units = max(len(units), 1)

    return {
        "coded_segment_count": len(units),
        "code_rename_count": len(renames),
        "code_creation_count": len(creations),
        "code_deletion_count": len(deletions),
        "code_definition_mod_count": len(def_mods),
        "codebook_change_count": len(codebook_changes),
        "codebook_change_with_reason": with_reason,
        "rename_with_reason": rename_with_reason,
        "rename_without_reason": max(0, len(renames) - rename_with_reason),
        "unresolved_disagreements": unresolved,
        "resolved_disagreements": resolved,
        "reflexive_memo_count": memos,
        "reflection_response_count": reflections,
        "span_sample_size": len(spans),
        "span_length_mean": statistics.mean(spans) if spans else None,
        "span_length_cv": span_cv,
        "segment_coding_share": segment_like / total_units,
        "whole_task_coding_share": whole_like / total_units,
        "event_count": len(events),
    }


# ── Inference ────────────────────────────────────────────────────────────────


def _score_from_rate(rate: float, *, soft_cap: float = 1.0) -> float:
    """Map a non-negative rate into 0–100 with diminishing returns."""
    if rate <= 0:
        return 0.0
    x = min(rate / soft_cap, 3.0)
    return 100.0 * (1.0 - math.exp(-x))


def infer_epistemology(agg: Dict[str, Any], rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rules = rules or load_rules()
    templates = rules.get("evidence_templates") or {}
    n = max(agg.get("coded_segment_count") or 0, 1)
    changes = agg.get("codebook_change_count") or 0
    renames = agg.get("code_rename_count") or 0

    codebook_change_rate = changes / n
    rename_rate = renames / n
    rename_reason_ratio = (
        (agg.get("rename_with_reason") or 0) / renames if renames else 0.5
    )
    change_note_coverage = (
        (agg.get("codebook_change_with_reason") or 0) / changes if changes else 0.5
    )
    def_mod_rate = (agg.get("code_definition_mod_count") or 0) / n

    # Dimension raw features → 0–100 contribution scores
    features = {
        "codebook_change_rate": _score_from_rate(codebook_change_rate, soft_cap=0.15),
        "rename_rate": _score_from_rate(rename_rate, soft_cap=0.1),
        "rename_with_reason_ratio": 100.0 * rename_reason_ratio,
        "option_definition_change_rate": _score_from_rate(def_mod_rate, soft_cap=0.1),
        "inverse_codebook_churn": 100.0 - _score_from_rate(codebook_change_rate, soft_cap=0.15),
        "inverse_rename_rate": 100.0 - _score_from_rate(rename_rate, soft_cap=0.1),
        "early_codebook_settled": 70.0 if changes <= max(2, n // 10) else 35.0,
        "span_length_variability": _clamp(100.0 * min((agg.get("span_length_cv") or 0) / 0.8, 1.0)),
        "unresolved_disagreement_share": _clamp(
            100.0
            * min(
                (agg.get("unresolved_disagreements") or 0)
                / max((agg.get("unresolved_disagreements") or 0)
                + (agg.get("resolved_disagreements") or 0), 1),
                1.0,
            )
        ),
        "resolved_disagreement_share": _clamp(
            100.0
            * min(
                (agg.get("resolved_disagreements") or 0)
                / max((agg.get("unresolved_disagreements") or 0)
                + (agg.get("resolved_disagreements") or 0), 1),
                1.0,
            )
        ),
        "segment_vs_whole_mix": _clamp(
            100.0
            * (
                1.0
                - abs(0.5 - (agg.get("segment_coding_share") or 0))
                * 2.0  # peak when mix near 50/50; still mid if all segment
            )
            if (agg.get("span_sample_size") or 0) > 0
            else 40.0
        ),
        "change_note_coverage": 100.0 * change_note_coverage,
        "memo_activity": _score_from_rate((agg.get("reflexive_memo_count") or 0) / n, soft_cap=0.2),
        "researcher_reflection_recorded": 80.0
        if (agg.get("reflection_response_count") or 0) > 0
        else 15.0,
    }

    scores: Dict[str, int] = {}
    for dim, cfg in (rules.get("dimensions") or {}).items():
        weights = cfg.get("weights") or {}
        total_w = sum(weights.values()) or 1.0
        val = 0.0
        for feat, w in weights.items():
            val += features.get(feat, 50.0) * (w / total_w)
        scores[dim] = _clamp(val)

    stance, evidence = _map_stance(scores, agg, rules, templates)
    return {
        "scores": scores,
        "inferredStance": stance,
        "evidence": evidence,
        "aggregation": agg,
        "features": {k: round(v, 1) for k, v in features.items()},
        "inferenceVersion": rules.get("version", 1),
    }


def _in_range(score: int, bounds: List[int]) -> bool:
    if not bounds or len(bounds) < 2:
        return True
    return bounds[0] <= score <= bounds[1]


def _map_stance(
    scores: Dict[str, int],
    agg: Dict[str, Any],
    rules: Dict[str, Any],
    templates: Dict[str, str],
) -> Tuple[str, List[str]]:
    evidence: List[str] = []
    n = agg.get("coded_segment_count") or 0
    threshold = rules.get("coded_segment_revelation_threshold", 20)

    if n < max(5, threshold // 4):
        evidence.append(templates.get("emerging", "Behavioral signals are still forming"))
        return "emerging", evidence

    mapping = rules.get("stance_mapping") or {}
    best = "emerging"
    best_hits = -1
    for stance, dims in mapping.items():
        hits = 0
        for dim, bounds in dims.items():
            if _in_range(scores.get(dim, 0), bounds):
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best = stance

    # Require majority of dimensions; else emerging
    need = max(2, len(next(iter(mapping.values()), {})) // 2 + 1) if mapping else 2
    if best_hits < need:
        best = "emerging"
        evidence.append(templates.get("emerging", "Behavioral signals are still forming"))

    if (agg.get("code_rename_count") or 0) >= 2:
        evidence.append(templates.get("high_rename", "Codes were revised multiple times"))
    if (agg.get("rename_without_reason") or 0) >= 2:
        evidence.append(
            templates.get(
                "rename_without_reason",
                "Several code renames lack a documented change note",
            )
        )
    elif (agg.get("rename_with_reason") or 0) >= 1:
        evidence.append(
            templates.get(
                "rename_with_reason",
                "Codebook changes often include a written rationale",
            )
        )
    if (agg.get("codebook_change_count") or 0) >= 3:
        evidence.append(
            templates.get(
                "high_codebook_churn",
                "The codebook continued to evolve across the coding period",
            )
        )
    elif n >= threshold and (agg.get("codebook_change_count") or 0) <= 1:
        evidence.append(
            templates.get(
                "stable_codebook",
                "Codebook structure remained relatively stable after early coding",
            )
        )
    if (agg.get("span_length_cv") or 0) >= 0.45:
        evidence.append(
            templates.get(
                "span_variable",
                "Coding span / selection length varies substantially",
            )
        )
    elif (agg.get("span_sample_size") or 0) >= 5 and (agg.get("span_length_cv") or 0) < 0.25:
        evidence.append(
            templates.get("span_uniform", "Coding units tend toward similar selection lengths")
        )
    if (agg.get("unresolved_disagreements") or 0) >= 1:
        evidence.append(
            templates.get(
                "unresolved_disagreements",
                "Several coder disagreements remain unresolved",
            )
        )
    if (agg.get("resolved_disagreements") or 0) >= 1:
        evidence.append(
            templates.get(
                "resolved_disagreements",
                "Disagreements were discussed and resolved through consensus",
            )
        )
    if (agg.get("reflexive_memo_count") or 0) >= 2:
        evidence.append(
            templates.get("memo_active", "Reflexive memos / task notes were written during coding")
        )
    elif n >= 10 and (agg.get("reflexive_memo_count") or 0) == 0:
        evidence.append(
            templates.get("memo_sparse", "Few reflexive memos accompany coding decisions")
        )

    # Deduplicate, keep order
    seen = set()
    uniq = []
    for e in evidence:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    return best, uniq[:8]


# ── Status / revelation / persistence ────────────────────────────────────────


async def build_inference_bundle(
    project_id: str,
    coder_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    rules = load_rules()
    units = await collect_coding_units(project_id, coder_user_id)
    events = await collect_research_events(project_id, coder_user_id)
    agg = aggregate_behavior(events, units)
    inferred = infer_epistemology(agg, rules)
    threshold = int(rules.get("coded_segment_revelation_threshold", 20))
    coded = agg["coded_segment_count"]

    state = await get_revelation_state(project_id, coder_user_id)
    already = bool(state.get("revelation_triggered"))
    ready = coded >= threshold

    return {
        "projectId": project_id,
        "coderUserId": coder_user_id,
        "codedSegmentCount": coded,
        "revelationThreshold": threshold,
        "revelationReady": ready,
        "revelationTriggered": already,
        "inferredStance": inferred["inferredStance"],
        "scores": inferred["scores"],
        "evidence": inferred["evidence"],
        "aggregation": agg,
        "inferenceVersion": inferred["inferenceVersion"],
        "coverageNotes": _coverage_notes(),
    }


def _coverage_notes() -> Dict[str, str]:
    return {
        "supported": (
            "activity_logs codebook diffs + change notes; task annotations; "
            "transcript_codings; passage_annotations; task_notes; consensus resolve/unresolved"
        ),
        "partial": (
            "coding span length inferred from text_snapshot/selected_text when present; "
            "whole-task text annotations have no char span; disagreement-created is derived"
        ),
        "not_yet": (
            "intentional uncoded segments; chat/discussion.recorded linkage; "
            "per-option rename API (only overwrite diffs)"
        ),
    }


async def get_revelation_state(
    project_id: str,
    coder_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    project_db = await get_project_db(project_id)
    if project_db is None:
        return {}
    q: Dict[str, Any] = {"scope": "coder" if coder_user_id else "project"}
    if coder_user_id:
        q["coder_user_id"] = str(coder_user_id)
    else:
        q["coder_user_id"] = None
    doc = await project_db.epistemology_state.find_one(q)
    return doc or {}


async def mark_revelation_triggered(
    project_id: str,
    coder_user_id: Optional[str],
    bundle: Dict[str, Any],
) -> None:
    project_db = await get_project_db(project_id)
    if project_db is None:
        return
    q = {
        "scope": "coder" if coder_user_id else "project",
        "coder_user_id": str(coder_user_id) if coder_user_id else None,
    }
    await project_db.epistemology_state.update_one(
        q,
        {
            "$set": {
                **q,
                "revelation_triggered": True,
                "revelation_at": datetime.utcnow(),
                "coded_segment_count": bundle.get("codedSegmentCount"),
                "inferred_stance": bundle.get("inferredStance"),
                "scores": bundle.get("scores"),
                "evidence": bundle.get("evidence"),
                "inference_version": bundle.get("inferenceVersion"),
            }
        },
        upsert=True,
    )


async def list_reflections(
    project_id: str,
    coder_user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    project_db = await get_project_db(project_id)
    if project_db is None:
        return []
    q: Dict[str, Any] = {}
    if coder_user_id:
        q["coder_user_id"] = str(coder_user_id)
    out = []
    async for doc in project_db.epistemology_reflections.find(q).sort([("timestamp", -1)]):
        doc["id"] = str(doc.pop("_id"))
        if isinstance(doc.get("timestamp"), datetime):
            doc["timestamp"] = _iso_ts(doc["timestamp"])
        out.append(doc)
    return out


async def save_reflection_response(
    project_id: str,
    *,
    coder_user_id: Optional[str],
    responding_user_id: str,
    system_inference: str,
    researcher_response: str,
    response_type: str,
    scores: Dict[str, Any],
    evidence_snapshot: List[str],
    inference_version: int,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    project_db = await get_project_db(project_id)
    if project_db is None:
        raise RuntimeError("Project DB unavailable")

    doc = {
        "project_id": project_id,
        "coder_user_id": str(coder_user_id) if coder_user_id else None,
        "responding_user_id": responding_user_id,
        "systemInference": system_inference,
        "researcherResponse": researcher_response,
        "responseType": response_type,  # confirm | correct | does_not_fit
        "scores": scores,
        "evidenceSnapshot": evidence_snapshot,
        "note": (note or "").strip() or None,
        "timestamp": datetime.utcnow(),
        "inferenceVersion": inference_version,
    }
    res = await project_db.epistemology_reflections.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    return doc


async def list_project_coders(project_id: str) -> List[Dict[str, str]]:
    """Coders who have coding units or memos in this project."""
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    ids: set = set()
    if project_db is not None:
        for coll in ("annotations", "transcript_codings", "passage_annotations", "task_notes"):
            try:
                uids = await project_db[coll].distinct("coder_user_id")
            except Exception:
                continue
            for uid in uids or []:
                if uid:
                    ids.add(str(uid))

    coders = []
    for uid in sorted(ids):
        oid = _oid(uid)
        name, email = uid, ""
        if oid:
            u = await core_db.users.find_one({"_id": oid}, {"name": 1, "email": 1, "username": 1})
            if u:
                name = u.get("name") or u.get("username") or uid
                email = u.get("email") or ""
        coders.append({"id": uid, "name": name, "email": email})
    return coders
