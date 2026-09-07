"""Quota-aware YouTube Data API v3 client. Never calls search.list."""
from __future__ import annotations

import logging

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from etl.errors import QuotaExceeded, is_quota_exceeded, redact_http_error
from etl.quota import LIST_CALL_UNITS, QuotaBudget

logger = logging.getLogger(__name__)


def build_youtube(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def execute(request, quota: QuotaBudget) -> dict:
    """Run one list() call: 1 unit. Stop locally before the call if remaining < 1."""
    quota.ensure(LIST_CALL_UNITS)
    try:
        response = request.execute()
    except HttpError as err:
        quota.record(LIST_CALL_UNITS)
        if is_quota_exceeded(err):
            logger.error("%s", redact_http_error(err))
            raise QuotaExceeded(redact_http_error(err)) from err
        raise
    quota.record(LIST_CALL_UNITS)
    return response
