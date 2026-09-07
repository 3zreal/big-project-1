"""TRANSFORM — clean and normalize raw data."""
import logging

import pandas as pd

from etl.utils import DATA_PROCESSED_DIR

logger = logging.getLogger(__name__)


def transform_videos(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the videos table: dtypes, timestamps, missing values."""
    DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    # TODO: cast dtypes, normalize timestamps, add extracted_at / ingestion_date
    raise NotImplementedError("transform_videos is not implemented yet")


def transform_comments(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the comments table."""
    DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    # TODO: cast dtypes, normalize timestamps, handle deleted comments / missing fields
    raise NotImplementedError("transform_comments is not implemented yet")
