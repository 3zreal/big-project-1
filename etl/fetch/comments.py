"""commentThreads.list, order=time, one page per video.

Returns a CommentFetchResult from every exit — including quota exhaustion — so
the caller has a single shape to handle instead of a return path and a raise path.
"""
from __future__ import annotations

import logging

import pandas as pd

from etl.errors import QuotaExceeded, QuotaStop, YoutubeApiError, is_comments_disabled
from etl.fetch.results import CommentFetchResult, StopReason
from etl.utils import dedupe
from etl.utils import write_json
from etl.youtube_api import execute

from .context import FetchContext

logger = logging.getLogger(__name__)

COMMENT_PAGE_SIZE = 100
COMMENT_BOOTSTRAP_PER_CHANNEL = 5

def select_comment_targets(
    videos: pd.DataFrame,
    watermark: dict[str, str] | None = None,
    *,
    bootstrap_per_channel: int = COMMENT_BOOTSTRAP_PER_CHANNEL,
) -> list[str]:
    """Steady-state: videos newer than watermark. Bootstrap: last 5 published/channel."""
    if videos.empty:
        return []
    marks = watermark or {}
    # Parse once for the whole frame; grouping stays in original channel order so the
    # fetch queue (and therefore what survives a mid-run quota stop) is unchanged.
    frame = videos[["channel_id", "video_id", "published_at"]].copy()
    frame["_pub"] = pd.to_datetime(frame["published_at"], utc=True, errors="coerce")
    targets: list[str] = []
    for channel_id, group in frame.groupby("channel_id", sort=False):
        ordered = group.sort_values("_pub", ascending=False)
        mark = marks.get(str(channel_id))
        if mark:
            mark_ts = pd.to_datetime(mark, utc=True, errors="coerce")
            targets.extend(ordered.loc[ordered["_pub"] > mark_ts, "video_id"].tolist())
        else:
            targets.extend(ordered["video_id"].head(bootstrap_per_channel).tolist())
    return dedupe(targets)


def fetch_comments(video_ids: list[str], *, ctx: FetchContext) -> CommentFetchResult:
    """One page of top-level comments per video. Skips commentsDisabled videos."""
    rows: list[dict] = []
    raw_items: list[dict] = []
    completed: list[str] = []
    skipped_disabled: list[str] = []
    failed: list[str] = []
    ids = list(video_ids)

    for index, video_id in enumerate(ids):
        try:
            resp = execute(
                ctx.client.commentThreads().list(
                    part="snippet",
                    videoId=video_id,
                    order="time",
                    maxResults=COMMENT_PAGE_SIZE,
                ),
                ctx.quota,
            )
        except (QuotaStop, QuotaExceeded) as err:
            stopped_by: StopReason = (
                "quota_exceeded" if isinstance(err, QuotaExceeded) else "quota_stop"
            )
            logger.warning(
                "%s before comments for %s; keeping %s rows and %s pending",
                stopped_by,
                video_id,
                len(rows),
                len(ids) - index,
            )
            return _result(
                ctx,
                raw_items,
                rows,
                # Everything not yet attempted still needs comments next run.
                pending=dedupe([*failed, *ids[index:]]),
                completed=completed,
                skipped=skipped_disabled,
                stopped_by=stopped_by,
                stop_detail=str(err),
            )
        except YoutubeApiError as err:
            if is_comments_disabled(err):
                logger.info("skip commentsDisabled/unavailable video %s", video_id)
                skipped_disabled.append(video_id)
                completed.append(video_id)
                continue
            logger.warning("commentThreads %s: %s", video_id, err)
            failed.append(video_id)
            continue

        if resp.get("nextPageToken"):
            logger.debug("comment nextPageToken ignored for %s (one-page cap)", video_id)
        completed.append(video_id)
        items = resp.get("items") or []
        raw_items.extend(items)
        rows.extend(comment_rows(items, video_id))

    return _result(
        ctx, raw_items, rows, pending=failed, completed=completed, skipped=skipped_disabled
    )


def comment_rows(items: list[dict], video_id: str) -> list[dict]:
    """Curated column names, so downstream transforms never rename."""
    rows = []
    for item in items:
        thread = item.get("snippet") or {}
        top = (thread.get("topLevelComment") or {}).get("snippet") or {}
        rows.append(
            {
                "comment_id": item.get("id", ""),
                "video_id": video_id,
                "parent_id": "",
                "author_name": top.get("authorDisplayName", ""),
                "comment_text": top.get("textOriginal") or top.get("textDisplay") or "",
                "like_count": int(top.get("likeCount") or 0),
                "reply_count": int(thread.get("totalReplyCount") or 0),
                "published_at": top.get("publishedAt", ""),
                "updated_at": top.get("updatedAt") or top.get("publishedAt", ""),
            }
        )
    return rows


def _result(
    ctx: FetchContext,
    raw_items: list[dict],
    rows: list[dict],
    *,
    pending: list[str],
    completed: list[str],
    skipped: list[str],
    stopped_by: StopReason | None = None,
    stop_detail: str = "",
) -> CommentFetchResult:
    """Every exit persists this pass and returns this shape."""
    write_json(ctx.run_dir / "comments.json", raw_items)
    ctx.dump_quota()
    if skipped:
        logger.info("comments disabled or unavailable on %s videos", len(skipped))
    return CommentFetchResult(
        frame=pd.DataFrame(rows),
        pending=dedupe(pending),
        completed=completed,
        skipped_disabled=skipped,
        stopped_by=stopped_by,
        stop_detail=stop_detail,
    )
