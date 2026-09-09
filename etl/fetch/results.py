"""Declared return types for the fetch stage.

These replace metadata smuggled through `DataFrame.attrs` and attributes bolted
onto exception instances: every field a later stage reads is named here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

# Why a run stopped early. None means the fetch ran to completion.
StopReason = Literal["quota_exceeded", "quota_stop"]


@dataclass(frozen=True)
class VideoFetchResult:
    """videos.list rows plus the playlist tail this run deliberately skipped."""

    frame: pd.DataFrame
    remainder: list[dict[str, Any]] = field(default_factory=list)

    def blocked_channel_ids(self) -> set[str]:
        """Channels whose unfetched remainder is newer than the prior watermark.

        These must not have their watermark advanced, or the skipped videos are
        never picked up again.
        """
        return {
            str(row["channel_id"]) for row in self.remainder if row.get("newer_than_watermark")
        }


@dataclass(frozen=True)
class CommentFetchResult:
    """commentThreads rows plus exactly which video IDs are resolved or still owed.

    Returned from every exit of `fetch_comments`, including quota exhaustion, so
    the caller has one shape to handle instead of a return path and a raise path.
    """

    frame: pd.DataFrame
    pending: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    skipped_disabled: list[str] = field(default_factory=list)
    stopped_by: StopReason | None = None
    stop_detail: str = ""

    @property
    def quota_exhausted(self) -> bool:
        """True when YouTube itself refused further calls (not the local budget)."""
        return self.stopped_by == "quota_exceeded"
