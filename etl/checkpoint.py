"""Resume state: sets of video_id, not playlist page tokens.

Local JSON is always written (gitignored). Also MERGE'd into
youtube_curated.fetch_checkpoint when that table exists.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from etl.utils import dedupe
from etl.utils import load_json, write_json
from etl.utils import DATA_PROCESSED_DIR

logger = logging.getLogger(__name__)

CHECKPOINT_PATH = DATA_PROCESSED_DIR / "fetch_checkpoint.json"
DEFAULT_KEY = "artist100"


def empty_checkpoint() -> dict[str, Any]:
    return {
        "checkpoint_key": DEFAULT_KEY,
        "comment_pending_video_ids": [],
        "completed_video_ids": [],
        "units_spent": 0,
        "pacific_date": "",
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


def resume_comment_targets(
    state: dict[str, Any], known_video_ids: set[str], fresh: list[str]
) -> list[str]:
    """Video IDs still owed comments: last run's leftovers first, then this run's picks.

    Leftovers are kept only when the video is still in the current fetch slice.
    """
    pending = [
        vid for vid in (state.get("comment_pending_video_ids") or []) if vid in known_video_ids
    ]
    return dedupe([*pending, *fresh])


def mark_videos_pending(state: dict[str, Any], video_ids: list[str]) -> dict[str, Any]:
    pending = set(state.get("comment_pending_video_ids") or [])
    done = set(state.get("completed_video_ids") or [])
    state["comment_pending_video_ids"] = sorted(pending | ({v for v in video_ids if v} - done))
    return state


def mark_videos_completed(state: dict[str, Any], video_ids: list[str]) -> dict[str, Any]:
    """Comments MERGEd or skipped as commentsDisabled — safe to drop from pending."""
    ids = {v for v in video_ids if v}
    pending = set(state.get("comment_pending_video_ids") or [])
    done = set(state.get("completed_video_ids") or [])
    state["comment_pending_video_ids"] = sorted(pending - ids)
    state["completed_video_ids"] = sorted(done | ids)
    return state


def maybe_advance_watermark(
    state: dict[str, Any],
    channel_id: str,
    published_at: str,
    blocked: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Advance only after the caller knows comments for that slice are durable.

    Skipped when unfetched playlist remainder is still newer than the prior mark;
    `blocked` comes from VideoFetchResult.blocked_channel_ids(), built once per batch.
    """
    if channel_id in blocked:
        logger.warning(
            "watermark not advanced for %s (unfetched remainder newer than prior mark)",
            channel_id,
        )
        return state
    if not published_at:
        return state
    marks = dict(state.get("watermarks") or {})
    marks[channel_id] = published_at
    state["watermarks"] = marks
    return state


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
    """Accepts the local JSON list form and the BigQuery JSON-string form."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
    return [str(v) for v in value if v]
