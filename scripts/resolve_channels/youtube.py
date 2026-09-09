"""YouTube Data API v3 calls: forHandle lookups and batch id validation.

Never uses search.list and never scrapes youtube.com/results.
"""
from __future__ import annotations

import logging

from googleapiclient.errors import HttpError

from etl.errors import redact_http_error as redact
from etl.utils import chunked, dedupe
from etl.youtube_api import ID_BATCH
from etl.youtube_api import build_youtube as build_client

from .names import handle_candidates, is_topic_channel, is_vevo_channel

logger = logging.getLogger("resolve_channels")

# Each forHandle guess costs a quota unit, and the retry passes re-guess the same
# handles; one answer per handle per process is enough.
_HANDLE_CACHE: dict[str, str | None] = {}


def lookup_handle(client, handle: str) -> str | None:
    """channels.list(forHandle=). Rejects Topic channels and unexpected VEVO hits."""
    handle = handle.lstrip("@")
    if handle in _HANDLE_CACHE:
        return _HANDLE_CACHE[handle]
    result = _lookup_handle_uncached(client, handle)
    _HANDLE_CACHE[handle] = result
    return result


def _lookup_handle_uncached(client, handle: str) -> str | None:
    try:
        resp = client.channels().list(part="id,snippet", forHandle=handle).execute()
    except HttpError as err:
        logger.debug("forHandle %s: %s", handle, redact(err))
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
    for batch in chunked(dedupe(channel_ids), ID_BATCH):
        try:
            resp = client.channels().list(part="snippet", id=",".join(batch)).execute()
        except HttpError as err:
            raise RuntimeError(redact(err)) from err
        for item in resp.get("items") or []:
            titles[item["id"]] = item.get("snippet", {}).get("title", "")
    return titles
