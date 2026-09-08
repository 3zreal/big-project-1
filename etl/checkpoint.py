"""Resume state: sets of video_id, not playlist page tokens.

Local JSON is always written (gitignored). Also MERGE'd into
youtube_curated.fetch_checkpoint when that table exists.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from etl.utils import DATA_PROCESSED_DIR, load_json, write_json

logger = logging.getLogger(__name__)

CHECKPOINT_PATH = DATA_PROCESSED_DIR / "fetch_checkpoint.json"
DEFAULT_KEY = "artist100"


def empty_checkpoint(*, pacific_date: str = "", units_spent: int = 0) -> dict[str, Any]:
    return {
        "checkpoint_key": DEFAULT_KEY,
        "comment_pending_video_ids": [],
        "completed_video_ids": [],
        "units_spent": units_spent,
        "pacific_date": pacific_date,
        "watermarks": {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def load_checkpoint() -> dict[str, Any]:
    payload = load_json(CHECKPOINT_PATH, default=None)
    if not payload:
        return empty_checkpoint()
    payload["comment_pending_video_ids"] = _as_id_list(payload.get("comment_pending_video_ids"))
    payload["completed_video_ids"] = _as_id_list(payload.get("completed_video_ids"))
    payload["watermarks"] = dict(payload.get("watermarks") or {})
    return payload


def save_checkpoint(state: dict[str, Any]) -> None:
    """Persist ID sets. Never store API keys."""
    pending = _as_id_list(state.get("comment_pending_video_ids"))
    completed = _as_id_list(state.get("completed_video_ids"))
    clean = {
        "checkpoint_key": state.get("checkpoint_key") or DEFAULT_KEY,
        "comment_pending_video_ids": pending,
        "completed_video_ids": completed,
        "units_spent": int(state.get("units_spent") or 0),
        "pacific_date": state.get("pacific_date") or "",
        "watermarks": dict(state.get("watermarks") or {}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(CHECKPOINT_PATH, clean)
    logger.info(
        "checkpoint pending=%s completed=%s pacific=%s units=%s",
        len(pending),
        len(completed),
        clean["pacific_date"],
        clean["units_spent"],
    )


def mark_videos_pending(state: dict[str, Any], video_ids: list[str]) -> dict[str, Any]:
    pending = set(state.get("comment_pending_video_ids") or [])
    done = set(state.get("completed_video_ids") or [])
    for vid in video_ids:
        if vid and vid not in done:
            pending.add(vid)
    state["comment_pending_video_ids"] = sorted(pending)
    return state


def mark_videos_completed(state: dict[str, Any], video_ids: list[str]) -> dict[str, Any]:
    """Comments MERGEd or skipped as commentsDisabled — safe to drop from pending."""
    pending = set(state.get("comment_pending_video_ids") or [])
    done = set(state.get("completed_video_ids") or [])
    for vid in video_ids:
        if not vid:
            continue
        pending.discard(vid)
        done.add(vid)
    state["comment_pending_video_ids"] = sorted(pending)
    state["completed_video_ids"] = sorted(done)
    return state


def set_watermark(state: dict[str, Any], channel_id: str, published_at: str) -> dict[str, Any]:
    """Advance only after the caller knows comments for that slice are durable."""
    marks = dict(state.get("watermarks") or {})
    marks[channel_id] = published_at
    state["watermarks"] = marks
    return state


def maybe_advance_watermark(
    state: dict[str, Any],
    channel_id: str,
    published_at: str,
    remainder: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Skip advance when unfetched playlist remainder is still newer than the prior mark."""
    blocked = any(
        (row.get("channel_id") == channel_id) and row.get("newer_than_watermark")
        for row in (remainder or [])
    )
    if blocked:
        logger.warning(
            "watermark not advanced for %s (unfetched remainder newer than prior mark)",
            channel_id,
        )
        return state
    if not published_at:
        return state
    return set_watermark(state, channel_id, published_at)


def checkpoint_row(state: dict[str, Any]) -> dict[str, Any]:
    """One BigQuery row: arrays stored as JSON strings."""
    return {
        "checkpoint_key": state.get("checkpoint_key") or DEFAULT_KEY,
        "comment_pending_video_ids": json.dumps(_as_id_list(state.get("comment_pending_video_ids"))),
        "completed_video_ids": json.dumps(_as_id_list(state.get("completed_video_ids"))),
        "units_spent": int(state.get("units_spent") or 0),
        "pacific_date": state.get("pacific_date") or "",
        "watermarks": json.dumps(state.get("watermarks") or {}),
        "updated_at": state.get("updated_at") or datetime.now(timezone.utc).isoformat(),
    }


def _as_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
    return [str(v) for v in value if v]
