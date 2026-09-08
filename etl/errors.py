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


class YoutubeApiError(Exception):
    """HttpError with API key stripped. Carries status + reason codes."""

    def __init__(self, message: str, *, status: int | None = None, reasons: list[str] | None = None):
        super().__init__(message)
        self.status = status
        self.reasons = reasons or []


class QuotaStop(Exception):
    """Local budget: remaining Pacific units cannot cover the next 1-unit call."""


class QuotaExceeded(Exception):
    """YouTube returned reason=quotaExceeded. Do not retry until Pacific midnight."""


def is_quota_exceeded(err: HttpError | YoutubeApiError | BaseException) -> bool:
    """True when Google billed this call as quota exhaustion.

    Check reason/message *before* treating a generic 403 as commentsDisabled.
    """
    reasons = err.reasons if isinstance(err, YoutubeApiError) else _reasons(err) if isinstance(err, HttpError) else []
    if any(reason == "quotaExceeded" for reason in reasons):
        return True
    return "quotaexceeded" in redact_http_error(err).lower()


def is_playlist_not_found(err: HttpError | YoutubeApiError | BaseException | str) -> bool:
    """Uploads playlist missing or private — skip this channel, do not abort the run."""
    reasons = err.reasons if isinstance(err, YoutubeApiError) else _reasons(err) if isinstance(err, HttpError) else []
    if any(reason == "playlistNotFound" for reason in reasons):
        return True
    status = err.status if isinstance(err, YoutubeApiError) else getattr(getattr(err, "resp", None), "status", None)
    if status == 404:
        return "playlist" in redact_http_error(err).lower()
    return "playlistnotfound" in redact_http_error(err).lower()


def is_comments_disabled(err: HttpError | YoutubeApiError) -> bool:
    """Skip this video only after quotaExceeded has been ruled out."""
    if is_quota_exceeded(err):
        return False
    skip = {"commentsDisabled", "videoNotFound", "processingFailure"}
    reasons = err.reasons if isinstance(err, YoutubeApiError) else _reasons(err)
    if any(reason in skip for reason in reasons):
        return True
    status = err.status if isinstance(err, YoutubeApiError) else getattr(err.resp, "status", None)
    return status == 403
