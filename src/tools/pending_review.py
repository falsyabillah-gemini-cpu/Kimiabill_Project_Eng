"""Multi-Slot Pending Review State Manager (`data/pending_review.json`).

Tracks each pending `unlisted` episode by `episode_id` so Slot 1 (`slot_2100`) and Slot 2 (`slot_0400`)
never overwrite each other across manual Telegram approval, revision, rejection, and AI Chief Editor fallback.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from src.brand_config import BASE_DIR

WIB = ZoneInfo("Asia/Jakarta")
PENDING_REVIEW_PATH = BASE_DIR / "data" / "pending_review.json"


def compute_target_publish_iso(slot: str, now_wib: Optional[datetime] = None) -> str:
    """Compute ISO-8601 WIB (+07:00) publishAt timestamp for slot_2100 or slot_0400."""
    now = (now_wib or datetime.now(WIB)).astimezone(WIB)
    if slot == "slot_0400":
        # Slot 2 is drafted the evening BEFORE (20:15 WIB), publishes 04:00 WIB next morning
        target = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if now.hour >= 12 or target <= now:
            target = target + timedelta(days=1)
        return target.isoformat(timespec="seconds")
    # Default Slot 1: 21:00 WIB same day (or immediate if already past 21:00)
    target = now.replace(hour=21, minute=0, second=0, microsecond=0)
    return target.isoformat(timespec="seconds")


def load_pending_reviews() -> dict[str, Any]:
    if not PENDING_REVIEW_PATH.exists():
        return {"schema_version": 1, "episodes": {}}
    try:
        raw = json.loads(PENDING_REVIEW_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("schema_version", 1)
            eps = raw.get("episodes")
            if not isinstance(eps, dict):
                raw["episodes"] = {}
            return raw
    except Exception:
        pass
    return {"schema_version": 1, "episodes": {}}


def save_pending_reviews(store: dict[str, Any]) -> None:
    eps = store.get("episodes") or {}
    if len(eps) > 40:
        # Keep most recent 40 records cleanly
        ordered = sorted(
            eps.items(),
            key=lambda kv: str((kv[1] or {}).get("created_at") or ""),
            reverse=True,
        )[:40]
        store["episodes"] = dict(ordered)
    PENDING_REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_REVIEW_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_pending_review(entry: dict[str, Any]) -> dict[str, Any]:
    ep_id = str(entry.get("episode_id") or "").strip()
    if not ep_id:
        raise ValueError("episode_id is required for pending_review")
    store = load_pending_reviews()
    now_iso = datetime.now(WIB).isoformat(timespec="seconds")
    existing = dict((store.get("episodes") or {}).get(ep_id) or {})
    merged = {
        "episode_id": ep_id,
        "slot": entry.get("slot") or existing.get("slot") or "slot_2100",
        "topic_title": entry.get("topic_title") or existing.get("topic_title") or "",
        "topic_slug": entry.get("topic_slug") or existing.get("topic_slug") or "",
        "pillar": entry.get("pillar") or existing.get("pillar") or "gadgets",
        "section_tag": entry.get("section_tag") or existing.get("section_tag") or "EVERYDAY_TECH",
        "source_url": entry.get("source_url") or existing.get("source_url") or "",
        "youtube_video_id": entry.get("youtube_video_id") or existing.get("youtube_video_id") or "",
        "youtube_url": entry.get("youtube_url") or existing.get("youtube_url") or "",
        "drive_url": entry.get("drive_url") or existing.get("drive_url") or "",
        "video_artifact_ref": entry.get("video_artifact_ref") or existing.get("video_artifact_ref") or "",
        "final_video_path": entry.get("final_video_path") or existing.get("final_video_path") or "",
        "thumbnail_path": entry.get("thumbnail_path") or existing.get("thumbnail_path") or "",
        "footage_tier_summary": entry.get("footage_tier_summary") or existing.get("footage_tier_summary") or "",
        "tts_engine": entry.get("tts_engine") or existing.get("tts_engine") or "Edge-TTS",
        "revision_count": int(entry.get("revision_count", existing.get("revision_count", 0)) or 0),
        "qa_summary": entry.get("qa_summary") or existing.get("qa_summary") or "",
        "pinned_comment": entry.get("pinned_comment", existing.get("pinned_comment", "")),
        "duration_sec": entry.get("duration_sec") or existing.get("duration_sec") or 0.0,
        "target_publish_iso": entry.get("target_publish_iso") or existing.get("target_publish_iso") or compute_target_publish_iso(entry.get("slot") or "slot_2100"),
        "telegram_message_id": entry.get("telegram_message_id", existing.get("telegram_message_id")),
        "reminder_sent": bool(entry.get("reminder_sent", existing.get("reminder_sent", False))),
        "status": entry.get("status") or existing.get("status") or "awaiting_review",
        "ai_audit_result": entry.get("ai_audit_result", existing.get("ai_audit_result")),
        "created_at": existing.get("created_at") or now_iso,
        "updated_at": now_iso,
    }
    store.setdefault("episodes", {})[ep_id] = merged
    save_pending_reviews(store)
    return merged


def get_pending_review(episode_id: str) -> Optional[dict[str, Any]]:
    store = load_pending_reviews()
    val = (store.get("episodes") or {}).get(episode_id)
    return dict(val) if isinstance(val, dict) else None


def find_episodes_by_status(status: str = "awaiting_review", slot: Optional[str] = None) -> list[dict[str, Any]]:
    store = load_pending_reviews()
    out: list[dict[str, Any]] = []
    for row in (store.get("episodes") or {}).values():
        if not isinstance(row, dict):
            continue
        if row.get("status") != status:
            continue
        if slot and row.get("slot") != slot:
            continue
        out.append(dict(row))
    out.sort(key=lambda r: str(r.get("created_at") or ""))
    return out


def mark_review_status(
    episode_id: str,
    status: str,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    row = get_pending_review(episode_id)
    if row is None:
        return None
    row["status"] = status
    if extra:
        row.update(extra)
    return upsert_pending_review(row)
