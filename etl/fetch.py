"""FETCH — YouTube Data API v3 slice for one run.

Last N=20 videos/channel, one playlistItems page (no nextPageToken loop).
Comments: order=time, max 1 page. Bootstrap = last 5 published/channel.

Do not import this from a second parallel API. main.py wiring is phase 4.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from googleapiclient.errors import HttpError

from etl.errors import QuotaExceeded, QuotaStop, is_comments_disabled, redact_http_error
from etl.quota import QuotaBudget
from etl.utils import (
    DATA_PROCESSED_DIR,
    load_env,
    load_json,
    new_run_id,
    raw_run_dir,
    require_env,
    write_json,
)
from etl.youtube_api import build_youtube, execute

logger = logging.getLogger(__name__)

VIDEOS_PER_CHANNEL = 20
PLAYLIST_PAGE_SIZE = 50
COMMENT_BOOTSTRAP_PER_CHANNEL = 5
COMMENT_PAGE_SIZE = 100
ID_BATCH = 50
PLAYLIST_CACHE = DATA_PROCESSED_DIR / "uploads_playlists.json"

_CTX: FetchContext | None = None


@dataclass
class FetchContext:
    client: Any
    quota: QuotaBudget
    run_id: str
    run_dir: Any
    playlist_ids: dict[str, str] = field(default_factory=dict)


def start_run(*, daily_limit: int | None = None) -> FetchContext:
    """Open quota + data/raw/{run_id}/. Call once per process unless tests pass ctx."""
    global _CTX
    load_env()
    api_key = require_env("YOUTUBE_API_KEY")
    run_id = new_run_id()
    ctx = FetchContext(
        client=build_youtube(api_key),
        quota=QuotaBudget(daily_limit=daily_limit),
        run_id=run_id,
        run_dir=raw_run_dir(run_id),
        playlist_ids=_load_playlist_cache(),
    )
    _CTX = ctx
    logger.info("fetch run_id=%s pacific=%s remaining=%s", run_id, ctx.quota.pacific_date, ctx.quota.remaining())
    return ctx


def get_context() -> FetchContext:
    return _CTX if _CTX is not None else start_run()


def _load_playlist_cache() -> dict[str, str]:
    raw = load_json(PLAYLIST_CACHE, default={})
    return {str(k): str(v) for k, v in raw.items() if str(v).startswith("UU")}


def _save_playlist_cache(mapping: dict[str, str]) -> None:
    PLAYLIST_CACHE.parent.mkdir(parents=True, exist_ok=True)
    write_json(PLAYLIST_CACHE, mapping)


def _chunks(values: list[str], size: int = ID_BATCH):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def fetch_channels(channel_ids: list[str], *, ctx: FetchContext | None = None) -> pd.DataFrame:
    """channels.list in batches of 50 (stats + contentDetails). ~2 units for 100 IDs."""
    ctx = ctx or get_context()
    wanted = list(dict.fromkeys(cid for cid in channel_ids if cid.startswith("UC")))
    items: list[dict] = []
    missing: list[str] = []

    for batch in _chunks(wanted):
        resp = execute(
            ctx.client.channels().list(
                part="snippet,statistics,contentDetails",
                id=",".join(batch),
                maxResults=ID_BATCH,
            ),
            ctx.quota,
        )
        found = {item["id"]: item for item in resp.get("items") or []}
        for cid in batch:
            if cid not in found:
                missing.append(cid)
                continue
            item = found[cid]
            uploads = (
                item.get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads")
                or ""
            )
            if uploads.startswith("UU"):
                ctx.playlist_ids[cid] = uploads
            items.append(item)

    if missing:
        logger.warning("channels.list omitted %s invalid ids (not retried): %s", len(missing), missing)

    _save_playlist_cache(ctx.playlist_ids)
    write_json(ctx.run_dir / "channels.json", items)
    write_json(ctx.run_dir / "quota.json", ctx.quota.snapshot())
    return _channels_frame(items)


def _channels_frame(items: list[dict]) -> pd.DataFrame:
    rows = []
    for item in items:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        uploads = (
            item.get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads")
            or ""
        )
        rows.append(
            {
                "channel_id": item["id"],
                "channel_title": snippet.get("title", ""),
                "published_at": snippet.get("publishedAt", ""),
                "subscriber_count": int(stats.get("subscriberCount") or 0),
                "view_count": int(stats.get("viewCount") or 0),
                "video_count": int(stats.get("videoCount") or 0),
                "uploads_playlist_id": uploads,
            }
        )
    return pd.DataFrame(rows)


def fetch_videos(
    channel_ids: list[str],
    n: int = VIDEOS_PER_CHANNEL,
    watermark: dict[str, str] | None = None,
    *,
    ctx: FetchContext | None = None,
) -> pd.DataFrame:
    """One playlistItems page per channel, keep up to n, then videos.list batches of 50.

    Does not paginate uploads. Remainder IDs on that page are logged and written to
    raw JSON so a later watermark must not jump past them.
    """
    ctx = ctx or get_context()
    need_playlists = [cid for cid in channel_ids if cid not in ctx.playlist_ids]
    if need_playlists:
        fetch_channels(need_playlists, ctx=ctx)

    picked: list[tuple[str, str, str]] = []  # channel_id, video_id, published_at
    remainder: list[dict] = []

    for channel_id in channel_ids:
        playlist_id = ctx.playlist_ids.get(channel_id)
        if not playlist_id:
            logger.warning("no uploads playlist for %s, skip", channel_id)
            continue
        # ONE page. Never loop on nextPageToken.
        resp = execute(
            ctx.client.playlistItems().list(
                part="contentDetails,snippet",
                playlistId=playlist_id,
                maxResults=PLAYLIST_PAGE_SIZE,
            ),
            ctx.quota,
        )
        page_items = resp.get("items") or []
        if resp.get("nextPageToken"):
            logger.info("playlist nextPageToken ignored for %s (one-page cap)", channel_id)

        parsed: list[tuple[str, str]] = []
        for item in page_items:
            details = item.get("contentDetails") or {}
            video_id = details.get("videoId") or ""
            published = details.get("videoPublishedAt") or (item.get("snippet") or {}).get("publishedAt") or ""
            if video_id:
                parsed.append((video_id, published))

        keep, rest = parsed[:n], parsed[n:]
        mark = (watermark or {}).get(channel_id, "")
        if mark:
            keep = [(vid, pub) for vid, pub in keep if pub > mark]
        for video_id, published in keep:
            picked.append((channel_id, video_id, published))
        if rest:
            logger.warning(
                "unfetched playlist remainder for %s: %s ids (do not raise watermark past them)",
                channel_id,
                len(rest),
            )
        for video_id, published in rest:
            remainder.append(
                {
                    "channel_id": channel_id,
                    "video_id": video_id,
                    "published_at": published,
                    "newer_than_watermark": bool(mark) and published > mark,
                }
            )

    write_json(ctx.run_dir / "playlist_remainder.json", remainder)

    video_ids = list(dict.fromkeys(vid for _, vid, _ in picked))
    raw_items: list[dict] = []
    for batch in _chunks(video_ids):
        resp = execute(
            ctx.client.videos().list(
                part="snippet,statistics,contentDetails,status",
                id=",".join(batch),
                maxResults=ID_BATCH,
            ),
            ctx.quota,
        )
        raw_items.extend(resp.get("items") or [])

    write_json(ctx.run_dir / "videos.json", raw_items)
    write_json(ctx.run_dir / "quota.json", ctx.quota.snapshot())
    frame = _videos_frame(raw_items)
    frame.attrs["remainder"] = remainder
    frame.attrs["run_id"] = ctx.run_id
    return frame


def _videos_frame(items: list[dict]) -> pd.DataFrame:
    rows = []
    for item in items:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        details = item.get("contentDetails") or {}
        rows.append(
            {
                "video_id": item["id"],
                "channel_id": snippet.get("channelId", ""),
                "title": snippet.get("title", ""),
                "published_at": snippet.get("publishedAt", ""),
                "duration": details.get("duration", ""),
                "view_count": int(stats.get("viewCount") or 0),
                "like_count": int(stats.get("likeCount") or 0),
                "comment_count": int(stats.get("commentCount") or 0),
            }
        )
    return pd.DataFrame(rows)


def select_comment_targets(
    videos: pd.DataFrame,
    watermark: dict[str, str] | None = None,
    *,
    bootstrap_per_channel: int = COMMENT_BOOTSTRAP_PER_CHANNEL,
) -> list[str]:
    """Steady-state: videos newer than watermark. Bootstrap: last 5 published/channel."""
    if videos.empty:
        return []
    targets: list[str] = []
    for channel_id, group in videos.groupby("channel_id", sort=False):
        ordered = group.sort_values("published_at", ascending=False)
        mark = (watermark or {}).get(str(channel_id))
        if mark:
            newer = ordered[ordered["published_at"] > mark]
            targets.extend(newer["video_id"].tolist())
        else:
            targets.extend(ordered.head(bootstrap_per_channel)["video_id"].tolist())
    return list(dict.fromkeys(targets))


def fetch_comments(
    video_ids: list[str],
    max_pages: int = 1,
    *,
    ctx: FetchContext | None = None,
) -> pd.DataFrame:
    """commentThreads.list, order=time, at most max_pages (default 1). Skip commentsDisabled."""
    if max_pages != 1:
        logger.warning("comment page cap is 1; ignoring max_pages=%s", max_pages)
    ctx = ctx or get_context()
    rows: list[dict] = []
    raw_items: list[dict] = []

    for video_id in video_ids:
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
        except QuotaStop:
            logger.warning("quota stop before comments for %s; returning %s rows", video_id, len(rows))
            break
        except QuotaExceeded:
            write_json(ctx.run_dir / "comments.json", raw_items)
            write_json(ctx.run_dir / "quota.json", ctx.quota.snapshot())
            raise
        except HttpError as err:
            if is_comments_disabled(err):
                logger.info("skip commentsDisabled/unavailable video %s", video_id)
                continue
            logger.warning("commentThreads %s: %s", video_id, redact_http_error(err))
            continue

        items = resp.get("items") or []
        if resp.get("nextPageToken"):
            logger.info("comment nextPageToken ignored for %s (one-page cap)", video_id)
        if not items:
            continue
        raw_items.extend(items)
        for item in items:
            top = ((item.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
            rows.append(
                {
                    "comment_id": item.get("id", ""),
                    "video_id": video_id,
                    "author": top.get("authorDisplayName", ""),
                    "text": top.get("textOriginal") or top.get("textDisplay") or "",
                    "like_count": int(top.get("likeCount") or 0),
                    "published_at": top.get("publishedAt", ""),
                }
            )

    write_json(ctx.run_dir / "comments.json", raw_items)
    write_json(ctx.run_dir / "quota.json", ctx.quota.snapshot())
    return pd.DataFrame(rows)
