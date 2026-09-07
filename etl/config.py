"""BigQuery table IDs from env. Does not create datasets."""
from __future__ import annotations

import os

from etl.utils import require_env


def project_id() -> str:
    return require_env("GCP_PROJECT_ID")


def dataset_raw() -> str:
    return os.getenv("BQ_DATASET_RAW", "youtube_raw")


def dataset_curated() -> str:
    return os.getenv("BQ_DATASET_CURATED", "youtube_curated")


def table(kind: str, name: str) -> str:
    """kind is 'raw' or 'curated'. Example: table('curated', 'videos')."""
    project = project_id()
    if kind == "curated":
        return f"{project}.{dataset_curated()}.{name}"
    if kind == "raw":
        return f"{project}.{dataset_raw()}.{name}"
    raise ValueError(f"unknown dataset kind {kind!r}")


def tables() -> dict[str, str]:
    return {
        "raw_videos": table("raw", "raw_videos"),
        "raw_comments": table("raw", "raw_comments"),
        "raw_channels": table("raw", "raw_channels"),
        "stg_videos": table("raw", "stg_videos"),
        "stg_comments": table("raw", "stg_comments"),
        "stg_channels": table("raw", "stg_channels"),
        "stg_snapshot": table("raw", "stg_channel_snapshot"),
        "artists": table("curated", "artists"),
        "videos": table("curated", "videos"),
        "comments": table("curated", "comments"),
        "channel_daily_snapshot": table("curated", "channel_daily_snapshot"),
        "pipeline_runs": table("curated", "pipeline_runs"),
        "fetch_checkpoint": table("curated", "fetch_checkpoint"),
    }
