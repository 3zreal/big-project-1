"""Quota-aware YouTube Data API v3 client. Never calls search.list."""
from __future__ import annotations

import logging

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from etl.errors import (
    QuotaExceeded,
    YoutubeApiError,
    is_comments_disabled,
    is_playlist_not_found,
    is_quota_exceeded,
)
from etl.quota import LIST_CALL_UNITS, QuotaBudget
from etl.retry import MAX_ATTEMPTS, should_retry, sleep_before_retry

logger = logging.getLogger(__name__)

# channels.list / videos.list accept at most 50 ids per call.
ID_BATCH = 50


def build_youtube(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def execute(request, quota: QuotaBudget) -> dict:
    """Run one list() call (1 unit). Retry 429/5xx only; never retry quotaExceeded."""
    last_err: YoutubeApiError | None = None
    for attempt in range(MAX_ATTEMPTS):
        quota.ensure(LIST_CALL_UNITS)
        try:
            response = request.execute()
        except HttpError as raw:
            # Translate once, here, so every branch below classifies the same object.
            err = YoutubeApiError.from_http(raw)
            last_err = err
            if is_quota_exceeded(err):
                quota.record(LIST_CALL_UNITS)
                logger.error("%s", err)
                raise QuotaExceeded(str(err)) from None
            if should_retry(err, attempt):
                logger.warning("%s", err)
                sleep_before_retry(attempt)
                continue
            quota.record(LIST_CALL_UNITS)
            if is_comments_disabled(err) or is_playlist_not_found(err):
                logger.debug("%s", err)
            else:
                logger.warning("%s", err)
            raise err from None
        quota.record(LIST_CALL_UNITS)
        return response
    assert last_err is not None
    raise last_err from None
