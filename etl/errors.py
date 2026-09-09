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

    @classmethod
    def from_http(cls, err: HttpError) -> "YoutubeApiError":
        """Single boundary translation: redact the key, keep status + reason codes."""
        return cls(
            redact_http_error(err),
            status=getattr(err.resp, "status", None),
            reasons=_reasons(err),
        )


class QuotaStop(Exception):
    """Local budget: remaining Pacific units cannot cover the next 1-unit call."""


class QuotaExceeded(Exception):
    """YouTube returned reason=quotaExceeded. Do not retry until Pacific midnight."""


def _reason_codes(err: HttpError | YoutubeApiError | BaseException | str) -> list[str]:
    """Reason codes the API itself returned, or [] when the payload is unreadable."""
    if isinstance(err, YoutubeApiError):
        return err.reasons
    return _reasons(err) if isinstance(err, HttpError) else []


def _status_code(err: HttpError | YoutubeApiError | BaseException | str) -> int | None:
    if isinstance(err, YoutubeApiError):
        return err.status
    return getattr(getattr(err, "resp", None), "status", None)


# Reason codes are the stable contract; the text checks below only cover a payload
# that failed to parse, where _reason_codes() comes back empty.
_SKIP_REASONS = frozenset({"commentsDisabled", "videoNotFound", "processingFailure"})


def is_quota_exceeded(err: HttpError | YoutubeApiError | BaseException) -> bool:
    """True when Google billed this call as quota exhaustion.

    Check reason/message *before* treating a generic 403 as commentsDisabled.
    """
    if "quotaExceeded" in _reason_codes(err):
        return True
    return "quotaexceeded" in redact_http_error(err).lower()


def is_playlist_not_found(err: HttpError | YoutubeApiError | BaseException | str) -> bool:
    """Uploads playlist missing or private — skip this channel, do not abort the run."""
    if "playlistNotFound" in _reason_codes(err):
        return True
    text = redact_http_error(err).lower()
    if _status_code(err) == 404:
        return "playlist" in text
    return "playlistnotfound" in text


def is_comments_disabled(err: HttpError | YoutubeApiError) -> bool:
    """Skip this video only after quotaExceeded has been ruled out."""
    if is_quota_exceeded(err):
        return False
    if not _SKIP_REASONS.isdisjoint(_reason_codes(err)):
        return True
    return _status_code(err) == 403
