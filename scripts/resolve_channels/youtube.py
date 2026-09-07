"""YouTube Data API v3 calls: forHandle lookups and batch id validation.

Never uses search.list and never scrapes youtube.com/results.
"""
from __future__ import annotations

import logging
import re

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .names import handle_candidates, is_topic_channel, is_vevo_channel

logger = logging.getLogger("resolve_channels")

# channels.list accepts at most 50 ids per call.
VALIDATE_BATCH = 50


def redact(text: str) -> str:
    """Strip the API key out of a googleapiclient error message."""
    return re.sub(r"key=[^&\s]+", "key=REDACTED", text)


def build_client(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def lookup_handle(client, handle: str) -> str | None:
    """channels.list(forHandle=). Rejects Topic channels and unexpected VEVO hits."""
    handle = handle.lstrip("@")
    try:
        resp = client.channels().list(part="id,snippet", forHandle=handle).execute()
    except HttpError as err:
        logger.debug("forHandle %s: %s", handle, redact(str(err)))
        return None

    items = resp.get("items") or []
    if not items:
        return None
    title = items[0].get("snippet", {}).get("title", "")
    if is_topic_channel(title):
        return None
    if is_vevo_channel(title) and "VEVO" not in handle.upper():
        return None
    return items[0]["id"]


def resolve_via_handles(client, artist_name: str, overrides: dict[str, str]) -> str | None:
    """First plausible @handle for this artist that resolves to a channel."""
    for handle in handle_candidates(artist_name, overrides):
        channel_id = lookup_handle(client, handle)
        if channel_id:
            return channel_id
    return None


def validate_channels(client, channel_ids: list[str]) -> dict[str, str]:
    """channel_id -> channel_title for the IDs that still exist."""
    titles: dict[str, str] = {}
    unique = list(dict.fromkeys(channel_ids))
    for start in range(0, len(unique), VALIDATE_BATCH):
        batch = unique[start : start + VALIDATE_BATCH]
        try:
            resp = client.channels().list(part="snippet", id=",".join(batch)).execute()
        except HttpError as err:
            raise RuntimeError(redact(str(err))) from err
        for item in resp.get("items") or []:
            titles[item["id"]] = item.get("snippet", {}).get("title", "")
    return titles
