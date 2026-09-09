"""YouTube error taxonomy: redact secrets, classify quota vs skippable failures.

`YoutubeApiError.from_http` is the single translation boundary. Everything past
`etl.youtube_api.execute` sees a YoutubeApiError, so the predicates below only
ever inspect a status code and reason codes the API itself returned.
"""
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


# Reason codes are the stable contract; the text checks below only cover a payload
# that failed to parse, where `reasons` comes back empty.
_SKIP_REASONS = frozenset({"commentsDisabled", "videoNotFound", "processingFailure"})


def is_quota_exceeded(err: YoutubeApiError) -> bool:
    """True when Google billed this call as quota exhaustion.

    Checked before treating a generic 403 as commentsDisabled.
    """
    if "quotaExceeded" in err.reasons:
        return True
    return "quotaexceeded" in str(err).lower()


def is_playlist_not_found(err: YoutubeApiError) -> bool:
    """Uploads playlist missing or private — skip this channel, do not abort the run."""
    if "playlistNotFound" in err.reasons:
        return True
    text = str(err).lower()
    if err.status == 404:
        return "playlist" in text
    return "playlistnotfound" in text


def is_comments_disabled(err: YoutubeApiError) -> bool:
    """Skip this video only after quotaExceeded has been ruled out."""
    if is_quota_exceeded(err):
        return False
    if not _SKIP_REASONS.isdisjoint(err.reasons):
        return True
    return err.status == 403
