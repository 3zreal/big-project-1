"""Pipeline orchestrator: fetch -> transform -> load.

Run:
    python main.py

Copy .env.example to .env and set GOOGLE_APPLICATION_CREDENTIALS before running.
"""
import os

from etl.fetch import fetch_comments, fetch_videos
from etl.load import load
from etl.transform import transform_comments, transform_videos
from etl.utils import load_env, require_env, setup_logging


def run() -> None:
    """Run one YouTube → BigQuery pipeline pass."""
    load_env()
    logger = setup_logging()

    project = require_env("GCP_PROJECT_ID")
    dataset_raw = os.getenv("BQ_DATASET_RAW", "youtube_raw")
    dataset_curated = os.getenv("BQ_DATASET_CURATED", "youtube_curated")

    table_videos = f"{project}.{dataset_curated}.videos"
    table_comments = f"{project}.{dataset_curated}.comments"
    _ = dataset_raw  # reserved for loading raw API responses into BigQuery

    logger.info("Starting YouTube pipeline")

    videos_raw = fetch_videos()
    comments_raw = fetch_comments()

    videos = transform_videos(videos_raw)
    comments = transform_comments(comments_raw)

    load(videos, table_videos)
    load(comments, table_comments)

    logger.info("Pipeline finished")


if __name__ == "__main__":
    run()
