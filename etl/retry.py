"""Retry 429 / 5xx only. Never retry quotaExceeded."""
from __future__ import annotations

import logging
import random
import time

from etl.errors import YoutubeApiError, is_quota_exceeded

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BASE_SLEEP_S = 1.0


def is_retryable(err: YoutubeApiError) -> bool:
    if is_quota_exceeded(err):
        return False
    status = err.status
    return status == 429 or (isinstance(status, int) and status >= 500)


def sleep_before_retry(attempt: int) -> None:
    """attempt is 0-based. Cap ~5 tries; jitter so two workers do not align."""
    delay = min(BASE_SLEEP_S * (2**attempt), 30.0) * (0.5 + random.random())
    logger.warning("retryable HTTP; sleeping %.1fs (attempt %s/%s)", delay, attempt + 1, MAX_ATTEMPTS)
    time.sleep(delay)


def should_retry(err: YoutubeApiError, attempt: int) -> bool:
    if not is_retryable(err):
        return False
    if attempt >= MAX_ATTEMPTS - 1:
        logger.warning("giving up after %s attempts: %s", MAX_ATTEMPTS, err)
        return False
    return True
