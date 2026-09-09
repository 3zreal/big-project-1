"""One video batch: MERGE videos → comments → checkpoint sets.

QuotaExceeded persists pending IDs and re-raises.
Watermark advances only after comments are durable or skipped as commentsDisabled.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pandas as pd

from etl.checkpoint import (
    blocked_channels,
    mark_videos_completed,
    mark_videos_pending,
    maybe_advance_watermark,
    save_checkpoint,
)
from etl.errors import QuotaExceeded
from etl.fetch import FetchContext, fetch_comments
from etl.load import land_and_merge, load_staging, merge_snapshot, persist_checkpoint_bq

logger = logging.getLogger(__name__)


def ingest_channel_facts(
    channels: pd.DataFrame,
    *,
    tables: dict[str, str],
    snapshot: pd.DataFrame | None = None,
) -> None:
    """MERGE artists from channels.list; optional same-day snapshot MERGE."""
    if channels is not None and not channels.empty:
        land_and_merge(
            channels,
            raw_table=tables["raw_channels"],
            stg_table=tables["stg_channels"],
            dest_table=tables["artists"],
            key="channel_id",
        )
    if snapshot is not None and not snapshot.empty:
        stg = tables["stg_snapshot"]
        load_staging(snapshot, stg, ("channel_id", "snapshot_date"))
        merge_snapshot(stg, tables["channel_daily_snapshot"], list(snapshot.columns))


def ingest_video_comment_batch(
    videos: pd.DataFrame,
    comment_video_ids: list[str],
    *,
    ctx: FetchContext,
    tables: dict[str, str],
    checkpoint: dict[str, Any],
    merge_checkpoint_table: str | None = None,
    remainder: list[dict[str, Any]] | None = None,
    prepare_comments: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """tables keys: raw_videos, stg_videos, videos, raw_comments, stg_comments, comments.

    MERGE comments *before* moving IDs to completed. quotaExceeded keeps those IDs pending.
    """
    if videos is not None and not videos.empty:
        land_and_merge(
            videos,
            raw_table=tables["raw_videos"],
            stg_table=tables["stg_videos"],
            dest_table=tables["videos"],
            key="video_id",
        )

    checkpoint = mark_videos_pending(checkpoint, comment_video_ids)
    _touch_quota(checkpoint, ctx)
    save_checkpoint(checkpoint)

    quota_error: QuotaExceeded | None = None
    try:
        comments = fetch_comments(comment_video_ids, ctx=ctx)
    except QuotaExceeded as err:
        quota_error = err
        comments = _frame_from_quota_error(err, comment_video_ids)

    # One durability order for both paths: MERGE comments, then move IDs to completed.
    comments = _prepare(comments, prepare_comments)
    _merge_comments(comments, tables)
    checkpoint = mark_videos_completed(checkpoint, list(comments.attrs.get("comment_completed") or []))

    if quota_error is None:
        if remainder is None and videos is not None:
            remainder = list(videos.attrs.get("remainder") or [])
        checkpoint = _advance_watermarks(checkpoint, videos, comment_video_ids, remainder or [])
    _touch_quota(checkpoint, ctx)
    save_checkpoint(checkpoint)
    if merge_checkpoint_table:
        persist_checkpoint_bq(checkpoint, merge_checkpoint_table)

    if quota_error is not None:
        pending = list(comments.attrs.get("comment_pending") or comment_video_ids)
        logger.warning(
            "quotaExceeded mid-comments; pending=%s (resume these video_ids)",
            len(pending),
        )
        raise quota_error
    return comments


def _frame_from_quota_error(err: QuotaExceeded, fallback_ids: list[str]) -> pd.DataFrame:
    """Partial comment rows the fetch had already collected when quota ran out."""
    frame = pd.DataFrame(list(getattr(err, "comment_rows", None) or []))
    frame.attrs["comment_pending"] = list(getattr(err, "comment_pending", None) or fallback_ids)
    frame.attrs["comment_completed"] = list(getattr(err, "comment_completed", None) or [])
    frame.attrs["skipped_disabled"] = list(getattr(err, "skipped_disabled", None) or [])
    return frame


def _prepare(comments: pd.DataFrame, prepare_comments) -> pd.DataFrame:
    if prepare_comments is None:
        return comments
    return prepare_comments(comments)


def _merge_comments(comments: pd.DataFrame, tables: dict[str, str]) -> None:
    if comments is None or comments.empty:
        return
    land_and_merge(
        comments,
        raw_table=tables["raw_comments"],
        stg_table=tables["stg_comments"],
        dest_table=tables["comments"],
        key="comment_id",
    )


def _touch_quota(checkpoint: dict[str, Any], ctx: FetchContext) -> None:
    checkpoint["units_spent"] = ctx.quota.units_spent
    checkpoint["pacific_date"] = ctx.quota.pacific_date


def _advance_watermarks(
    checkpoint: dict[str, Any],
    videos: pd.DataFrame | None,
    comment_video_ids: list[str],
    remainder: list[dict[str, Any]],
) -> dict[str, Any]:
    if videos is None or videos.empty or "channel_id" not in videos.columns:
        return checkpoint
    pending = set(checkpoint.get("comment_pending_video_ids") or [])
    comment_set = set(comment_video_ids)
    blocked = blocked_channels(remainder)
    for channel_id, group in videos.groupby("channel_id", sort=False):
        cid = str(channel_id)
        channel_targets = [vid for vid in group["video_id"].tolist() if vid in comment_set]
        if any(vid in pending for vid in channel_targets):
            continue
        newest_val = pd.to_datetime(group["published_at"], utc=True, errors="coerce").max()
        newest = newest_val.strftime("%Y-%m-%dT%H:%M:%SZ") if pd.notna(newest_val) else ""
        checkpoint = maybe_advance_watermark(checkpoint, cid, newest, blocked)
    return checkpoint
