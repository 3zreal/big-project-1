"""One video batch: MERGE videos → comments → checkpoint sets.

Phase 4 calls this from run(). QuotaExceeded persists pending IDs and re-raises.
Watermark advances only after comments are durable or skipped as commentsDisabled.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from etl.checkpoint import (
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
        stg = tables.get("stg_snapshot") or tables["stg_channels"]
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

    try:
        comments = fetch_comments(comment_video_ids, ctx=ctx)
    except QuotaExceeded as err:
        comments = pd.DataFrame(list(getattr(err, "comment_rows", None) or []))
        completed = list(getattr(err, "comment_completed", None) or [])
        pending = list(getattr(err, "comment_pending", None) or comment_video_ids)
        _merge_comments(comments, tables)
        checkpoint = mark_videos_completed(checkpoint, completed)
        _touch_quota(checkpoint, ctx)
        save_checkpoint(checkpoint)
        if merge_checkpoint_table:
            persist_checkpoint_bq(checkpoint, merge_checkpoint_table)
        logger.warning(
            "quotaExceeded mid-comments; pending=%s (resume these video_ids)",
            len(pending),
        )
        raise

    completed = list(comments.attrs.get("comment_completed") or [])
    _merge_comments(comments, tables)
    checkpoint = mark_videos_completed(checkpoint, completed)
    if remainder is None and videos is not None:
        remainder = list(videos.attrs.get("remainder") or [])
    checkpoint = _advance_watermarks(checkpoint, videos, comment_video_ids, remainder or [])
    _touch_quota(checkpoint, ctx)
    save_checkpoint(checkpoint)
    if merge_checkpoint_table:
        persist_checkpoint_bq(checkpoint, merge_checkpoint_table)
    return comments


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
    for channel_id, group in videos.groupby("channel_id", sort=False):
        cid = str(channel_id)
        channel_targets = [vid for vid in group["video_id"].tolist() if vid in comment_set]
        if any(vid in pending for vid in channel_targets):
            continue
        newest = str(group["published_at"].max() or "") if "published_at" in group.columns else ""
        checkpoint = maybe_advance_watermark(checkpoint, cid, newest, remainder)
    return checkpoint
