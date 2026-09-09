"""One uploads-playlist page per channel, then videos.list in batches of 50.

Never paginates uploads. Videos on that page beyond the cap are recorded as
remainder so a later watermark cannot jump past them.
"""
from __future__ import annotations

import logging

import pandas as pd

from etl.errors import YoutubeApiError, is_playlist_not_found
from etl.fetch.results import VideoFetchResult
from etl.utils import chunked, dedupe
from etl.utils import write_json
from etl.youtube_api import ID_BATCH, execute

from .channels import fetch_channels
from .context import FetchContext
from .context import save_playlist_cache

logger = logging.getLogger(__name__)

VIDEOS_PER_CHANNEL = 20
PLAYLIST_PAGE_SIZE = 50


def fetch_videos(
    channel_ids: list[str],
    n: int = VIDEOS_PER_CHANNEL,
    watermark: dict[str, str] | None = None,
    *,
    ctx: FetchContext,
) -> VideoFetchResult:
    """Pick up to n videos newer than the watermark per channel, then hydrate them."""
    _ensure_playlist_ids(channel_ids, ctx=ctx)

    picked: list[str] = []
    remainder: list[dict] = []

    for channel_id in channel_ids:
        page = _uploads_page(channel_id, ctx=ctx)
        if page is None:
            continue
        mark = (watermark or {}).get(channel_id, "")
        keep, rest = split_page(page, n=n, mark=mark)
        picked.extend(video_id for video_id, _ in keep)
        if rest:
            logger.warning(
                "unfetched playlist remainder for %s: %s ids (do not raise watermark past them)",
                channel_id,
                len(rest),
            )
        remainder.extend(
            {
                "channel_id": channel_id,
                "video_id": video_id,
                "published_at": published,
                "newer_than_watermark": bool(mark) and published > mark,
            }
            for video_id, published in rest
        )

    write_json(ctx.run_dir / "playlist_remainder.json", remainder)
    raw_items = _hydrate_videos(dedupe(picked), ctx=ctx)
    write_json(ctx.run_dir / "videos.json", raw_items)
    ctx.dump_quota()
    return VideoFetchResult(frame=videos_frame(raw_items), remainder=remainder)


def _ensure_playlist_ids(channel_ids: list[str], *, ctx: FetchContext) -> None:
    """Resolve uploads playlists for channels this run has not asked about yet."""
    unknown = [
        cid
        for cid in channel_ids
        if cid not in ctx.playlist_ids and cid not in ctx.attempted_channels
    ]
    if unknown:
        fetch_channels(unknown, ctx=ctx)


def _uploads_page(channel_id: str, *, ctx: FetchContext) -> list[dict] | None:
    """One playlistItems page, or None when the channel must be skipped."""
    playlist_id = ctx.playlist_ids.get(channel_id)
    if not playlist_id:
        logger.warning("no uploads playlist for %s, skip", channel_id)
        return None
    try:
        # ONE page. Never loop on nextPageToken.
        resp = execute(
            ctx.client.playlistItems().list(
                part="contentDetails,snippet",
                playlistId=playlist_id,
                maxResults=PLAYLIST_PAGE_SIZE,
            ),
            ctx.quota,
        )
    except YoutubeApiError as err:
        if is_playlist_not_found(err):
            logger.warning(
                "uploads playlist missing for %s (%s); skip channel", channel_id, playlist_id
            )
            ctx.playlist_ids.pop(channel_id, None)
            save_playlist_cache(ctx.playlist_ids)
            return None
        raise
    if resp.get("nextPageToken"):
        logger.debug("playlist nextPageToken ignored for %s (one-page cap)", channel_id)
    return resp.get("items") or []


def split_page(
    items: list[dict], *, n: int, mark: str
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split one playlist page into (fetch now, remainder). Pure — the watermark rule.

    Returns (video_id, published_at) pairs. `keep` drops anything at or before
    the watermark; `rest` is everything past the per-channel cap.
    """
    parsed: list[tuple[str, str]] = []
    for item in items:
        details = item.get("contentDetails") or {}
        video_id = details.get("videoId") or ""
        published = (
            details.get("videoPublishedAt")
            or (item.get("snippet") or {}).get("publishedAt")
            or ""
        )
        if video_id:
            parsed.append((video_id, published))

    keep, rest = parsed[:n], parsed[n:]
    if mark:
        keep = [(vid, pub) for vid, pub in keep if pub > mark]
    return keep, rest


def _hydrate_videos(video_ids: list[str], *, ctx: FetchContext) -> list[dict]:
    """videos.list in batches of 50 for the IDs this run selected."""
    raw_items: list[dict] = []
    for batch in chunked(video_ids, ID_BATCH):
        resp = execute(
            ctx.client.videos().list(
                part="snippet,statistics,contentDetails,status",
                id=",".join(batch),
                maxResults=ID_BATCH,
            ),
            ctx.quota,
        )
        raw_items.extend(resp.get("items") or [])
    return raw_items


def videos_frame(items: list[dict]) -> pd.DataFrame:
    """Curated column names, so downstream transforms never rename."""
    rows = []
    for item in items:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        details = item.get("contentDetails") or {}
        tags = snippet.get("tags") or []
        rows.append(
            {
                "video_id": item["id"],
                "channel_id": snippet.get("channelId", ""),
                "video_title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "published_at": snippet.get("publishedAt", ""),
                "duration": details.get("duration", ""),
                "tags": "|".join(str(t) for t in tags),
                "category_id": str(snippet.get("categoryId") or ""),
                "view_count": int(stats.get("viewCount") or 0),
                "like_count": int(stats.get("likeCount") or 0),
                "comment_count": int(stats.get("commentCount") or 0),
            }
        )
    return pd.DataFrame(rows)
