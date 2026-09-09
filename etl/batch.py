"""One video batch: MERGE videos → comments → checkpoint sets.

Watermarks advance only after comments are durable or skipped as commentsDisabled.
On quotaExceeded the durability work still runs, then the error is re-raised.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pandas as pd

from etl.checkpoint import (
    mark_videos_completed,
    mark_videos_pending,
    maybe_advance_watermark,
    save_checkpoint,
)
from etl.config import Tables
from etl.errors import QuotaExceeded
from etl.fetch import FetchContext, fetch_comments
from etl.fetch.results import CommentFetchResult
from etl.load import land_and_merge, load_staging, merge_from_staging, persist_checkpoint_bq

logger = logging.getLogger(__name__)

PrepareComments = Callable[[pd.DataFrame], pd.DataFrame]


def ingest_channel_facts(
    channels: pd.DataFrame,
    *,
    tables: Tables,
    snapshot: pd.DataFrame | None = None,
) -> None:
    """MERGE artists from channels.list; optional same-day snapshot MERGE."""
    if channels is not None and not channels.empty:
        land_and_merge(
            channels,
            raw_table=tables.raw_channels,
            stg_table=tables.stg_channels,
            dest_table=tables.artists,
            key=("channel_id",),
        )
    if snapshot is not None and not snapshot.empty:
        # Same-day re-run updates the row instead of adding one.
        key = ("channel_id", "snapshot_date")
        load_staging(snapshot, tables.stg_snapshot, key)
        merge_from_staging(
            tables.stg_snapshot, tables.channel_daily_snapshot, key, list(snapshot.columns)
        )


def ingest_video_comment_batch(
    videos: pd.DataFrame,
    comment_video_ids: list[str],
    *,
    ctx: FetchContext,
    tables: Tables,
    checkpoint: dict[str, Any],
    remainder_blocked: set[str],
    prepare_comments: PrepareComments,
) -> CommentFetchResult:
    """MERGE videos, then comments, then move IDs to completed — in that order.

    Raises QuotaExceeded only after everything already fetched is durable.
    """
    if videos is not None and not videos.empty:
        land_and_merge(
            videos,
            raw_table=tables.raw_videos,
            stg_table=tables.stg_videos,
            dest_table=tables.videos,
            key=("video_id",),
        )

    checkpoint = mark_videos_pending(checkpoint, comment_video_ids)
    _touch_quota(checkpoint, ctx)
    save_checkpoint(checkpoint)

    result = fetch_comments(comment_video_ids, ctx=ctx)

    # One durability order for every outcome: MERGE comments, then mark completed.
    comments = prepare_comments(result.frame)
    if not comments.empty:
        land_and_merge(
            comments,
            raw_table=tables.raw_comments,
            stg_table=tables.stg_comments,
            dest_table=tables.comments,
            key=("comment_id",),
        )
    checkpoint = mark_videos_completed(checkpoint, result.completed)

    if not result.quota_exhausted:
        checkpoint = _advance_watermarks(
            checkpoint, videos, comment_video_ids, remainder_blocked
        )
    _touch_quota(checkpoint, ctx)
    save_checkpoint(checkpoint)
    persist_checkpoint_bq(checkpoint, tables.fetch_checkpoint)

    if result.quota_exhausted:
        logger.warning(
            "quotaExceeded mid-comments; pending=%s (resume these video_ids)",
            len(result.pending),
        )
        raise QuotaExceeded(result.stop_detail or "quota exhausted during commentThreads")
    return CommentFetchResult(
        frame=comments,
        pending=result.pending,
        completed=result.completed,
        skipped_disabled=result.skipped_disabled,
        stopped_by=result.stopped_by,
        stop_detail=result.stop_detail,
    )


def _touch_quota(checkpoint: dict[str, Any], ctx: FetchContext) -> None:
    checkpoint["units_spent"] = ctx.quota.units_spent
    checkpoint["pacific_date"] = ctx.quota.pacific_date


def _advance_watermarks(
    checkpoint: dict[str, Any],
    videos: pd.DataFrame | None,
    comment_video_ids: list[str],
    blocked: set[str],
) -> dict[str, Any]:
    if videos is None or videos.empty or "channel_id" not in videos.columns:
        return checkpoint
    pending = set(checkpoint.get("comment_pending_video_ids") or [])
    comment_set = set(comment_video_ids)
    for channel_id, group in videos.groupby("channel_id", sort=False):
        cid = str(channel_id)
        channel_targets = [vid for vid in group["video_id"].tolist() if vid in comment_set]
        if any(vid in pending for vid in channel_targets):
            continue
        # published_at is already datetime64[UTC] out of transform_videos.
        newest_val = group["published_at"].max()
        newest = newest_val.strftime("%Y-%m-%dT%H:%M:%SZ") if pd.notna(newest_val) else ""
        checkpoint = maybe_advance_watermark(checkpoint, cid, newest, blocked)
    return checkpoint
