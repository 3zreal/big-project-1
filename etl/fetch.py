"""FETCH — pull data from YouTube Data API v3 and write raw files to data/raw/."""
import logging

import pandas as pd

from etl.utils import DATA_RAW_DIR

logger = logging.getLogger(__name__)


def fetch_videos() -> pd.DataFrame:
    """Fetch videos for the selected scope. Write raw JSON/CSV, then return a DataFrame."""
    DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    # TODO: call YouTube Data API v3 (videos.list / playlistItems.list)
    raise NotImplementedError("fetch_videos is not implemented yet")


def fetch_comments() -> pd.DataFrame:
    """Fetch comments for fetched videos. Write raw files, then return a DataFrame."""
    DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    # TODO: call commentThreads.list; handle quota limits and comments-disabled videos
    raise NotImplementedError("fetch_comments is not implemented yet")
