"""channels.list — stats, snippet and the uploads playlist ID, batched 50 per call."""
from __future__ import annotations

import logging

import pandas as pd

from etl.utils import chunked, dedupe
from etl.utils import write_json
from etl.youtube_api import ID_BATCH, execute

from .context import FetchContext
from .context import FetchContext, save_playlist_cache, uploads_id

logger = logging.getLogger(__name__)


def fetch_channels(channel_ids: list[str], *, ctx: FetchContext) -> pd.DataFrame:
    """channels.list in batches of 50 (stats + contentDetails). ~2 units for 100 IDs."""
    wanted = [cid for cid in dedupe(channel_ids) if cid.startswith("UC")]
    items: list[dict] = []
    missing: list[str] = []

    for batch in chunked(wanted, ID_BATCH):
        resp = execute(
            ctx.client.channels().list(
                part="snippet,statistics,contentDetails",
                id=",".join(batch),
                maxResults=ID_BATCH,
            ),
            ctx.quota,
        )
        found = {item["id"]: item for item in resp.get("items") or []}
        ctx.attempted_channels.update(batch)
        for cid in batch:
            item = found.get(cid)
            if item is None:
                missing.append(cid)
                continue
            uploads = uploads_id(item)
            if uploads.startswith("UU"):
                ctx.playlist_ids[cid] = uploads
            items.append(item)

    if missing:
        logger.warning("channels.list omitted %s invalid ids (not retried): %s", len(missing), missing)

    save_playlist_cache(ctx.playlist_ids)
    write_json(ctx.run_dir / "channels.json", items)
    ctx.dump_quota()
    return channels_frame(items)


def channels_frame(items: list[dict]) -> pd.DataFrame:
    """Curated column names, so downstream transforms never rename."""
    rows = []
    for item in items:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        rows.append(
            {
                "channel_id": item["id"],
                "channel_title": snippet.get("title", ""),
                "published_at": snippet.get("publishedAt", ""),
                "subscriber_count": int(stats.get("subscriberCount") or 0),
                "view_count": int(stats.get("viewCount") or 0),
                "video_count": int(stats.get("videoCount") or 0),
                "uploads_playlist_id": uploads_id(item),
            }
        )
    return pd.DataFrame(rows)
