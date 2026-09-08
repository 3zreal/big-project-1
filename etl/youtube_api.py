"""Quota-aware YouTube Data API v3 client. Never calls search.list."""
from __future__ import annotations

import logging

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from etl.errors import (
    QuotaExceeded,
    YoutubeApiError,
    _reasons,
    is_comments_disabled,
    is_playlist_not_found,
    is_quota_exceeded,
    redact_http_error,
)
from etl.quota import LIST_CALL_UNITS, QuotaBudget
from etl.retry import MAX_ATTEMPTS, should_retry, sleep_before_retry

logger = logging.getLogger(__name__)


def build_youtube(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def execute(request, quota: QuotaBudget) -> dict:
    """Run one list() call (1 unit). Retry 429/5xx only; never retry quotaExceeded."""
    last_err: HttpError | None = None
    for attempt in range(MAX_ATTEMPTS):
        quota.ensure(LIST_CALL_UNITS)
        try:
            response = request.execute()
        except HttpError as err:
            last_err = err
            redacted = redact_http_error(err)
            if is_quota_exceeded(err):
                quota.record(LIST_CALL_UNITS)
                logger.error("%s", redacted)
                raise QuotaExceeded(redacted) from None
            if should_retry(err, attempt):
                logger.warning("%s", redacted)
                sleep_before_retry(attempt)
                continue
            quota.record(LIST_CALL_UNITS)
            wrapped = YoutubeApiError(
                redacted,
                status=getattr(err.resp, "status", None),
                reasons=_reasons(err),
            )
            if is_comments_disabled(wrapped) or is_playlist_not_found(wrapped):
                logger.debug("%s", redacted)
            else:
                logger.warning("%s", redacted)
            raise wrapped from None
        quota.record(LIST_CALL_UNITS)
        return response
    assert last_err is not None
    raise YoutubeApiError(
        redact_http_error(last_err),
        status=getattr(last_err.resp, "status", None),
        reasons=_reasons(last_err),
    ) from None
