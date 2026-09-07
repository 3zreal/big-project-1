"""YouTube HttpError helpers: redact secrets, classify quota vs comments-disabled."""
from __future__ import annotations

import json
import re

from googleapiclient.errors import HttpError

_KEY_QUERY = re.compile(r"([?&]key=)[^&\s]+", re.IGNORECASE)


def redact_http_error(err: HttpError | BaseException | str) -> str:
    """Strip API keys from googleapiclient error text before logging."""
    return _KEY_QUERY.sub(r"\1REDACTED", str(err))


def _reasons(err: HttpError) -> list[str]:
    try:
        payload = json.loads(err.content.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return []
    errors = payload.get("error", {}).get("errors") or []
    return [str(item.get("reason") or "") for item in errors]


class QuotaStop(Exception):
    """Local budget: remaining Pacific units cannot cover the next 1-unit call."""


class QuotaExceeded(Exception):
    """YouTube returned reason=quotaExceeded. Do not retry until Pacific midnight."""


def is_quota_exceeded(err: HttpError) -> bool:
    """True when Google billed this call as quota exhaustion.

    Check reason/message *before* treating a generic 403 as commentsDisabled.
    """
    if any(reason == "quotaExceeded" for reason in _reasons(err)):
        return True
    return "quotaexceeded" in redact_http_error(err).lower()


def is_comments_disabled(err: HttpError) -> bool:
    """Skip this video only after quotaExceeded has been ruled out."""
    if is_quota_exceeded(err):
        return False
    skip = {"commentsDisabled", "videoNotFound", "processingFailure"}
    if any(reason in skip for reason in _reasons(err)):
        return True
    return getattr(err.resp, "status", None) == 403
