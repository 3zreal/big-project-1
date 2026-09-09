"""FETCH — YouTube Data API v3 slice for one run.

Last N=20 videos/channel, one playlistItems page (no nextPageToken loop).
Comments: order=time, max 1 page. Bootstrap = last 5 published/channel.

Split by API surface: context (client/quota/caches), channels, videos, comments.
Tuning constants stay in their own module (etl.fetch.videos.VIDEOS_PER_CHANNEL etc).
"""
from __future__ import annotations

from .channels import fetch_channels
from .comments import fetch_comments, select_comment_targets
from .context import FetchContext, start_run
from .results import CommentFetchResult, VideoFetchResult
from .videos import fetch_videos

__all__ = [
    "CommentFetchResult",
    "FetchContext",
    "VideoFetchResult",
    "fetch_channels",
    "fetch_comments",
    "fetch_videos",
    "select_comment_targets",
    "start_run",
]
