"""BigQuery table IDs from env, as a typed object. Does not create datasets."""
from __future__ import annotations

import os
from dataclasses import dataclass

from etl.utils import require_env


def project_id() -> str:
    return require_env("GCP_PROJECT_ID")


def dataset_raw() -> str:
    return os.getenv("BQ_DATASET_RAW", "youtube_raw")


def dataset_curated() -> str:
    return os.getenv("BQ_DATASET_CURATED", "youtube_curated")


@dataclass(frozen=True)
class Tables:
    """Fully-qualified table IDs. Attribute access, so a typo fails at lint time."""

    raw_videos: str
    raw_comments: str
    raw_channels: str
    stg_videos: str
    stg_comments: str
    stg_channels: str
    stg_snapshot: str
    artists: str
    videos: str
    comments: str
    channel_daily_snapshot: str
    pipeline_runs: str
    fetch_checkpoint: str


def tables() -> Tables:
    project = project_id()
    raw = f"{project}.{dataset_raw()}"
    curated = f"{project}.{dataset_curated()}"
    return Tables(
        raw_videos=f"{raw}.raw_videos",
        raw_comments=f"{raw}.raw_comments",
        raw_channels=f"{raw}.raw_channels",
        stg_videos=f"{raw}.stg_videos",
        stg_comments=f"{raw}.stg_comments",
        stg_channels=f"{raw}.stg_channels",
        stg_snapshot=f"{raw}.stg_channel_snapshot",
        artists=f"{curated}.artists",
        videos=f"{curated}.videos",
        comments=f"{curated}.comments",
        channel_daily_snapshot=f"{curated}.channel_daily_snapshot",
        pipeline_runs=f"{curated}.pipeline_runs",
        fetch_checkpoint=f"{curated}.fetch_checkpoint",
    )
