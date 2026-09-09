"""TRANSFORM — dtypes, lineage, curated field names.

Pure DataFrame -> DataFrame. Called after fetch, before staging load. The fetch
builders already emit curated column names, so nothing is renamed here.
tags stay STRING.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

SOURCE = "youtube_data_api_v3"


def _lineage(run_id: str, extracted_at: datetime) -> dict:
    """Provenance tail; the keys match table_schemas.LINEAGE."""
    ts = extracted_at.astimezone(timezone.utc)
    return {
        "extracted_at": ts,
        "ingestion_date": ts.date(),
        "source": SOURCE,
        "run_id": run_id,
    }


def _with_lineage(out: pd.DataFrame, run_id: str, extracted_at: datetime) -> pd.DataFrame:
    for key, val in _lineage(run_id, extracted_at).items():
        out[key] = val
    return out


def _as_ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0).astype("int64")


def _str(series: pd.Series) -> pd.Series:
    return series.fillna("").astype("string")


def _lookup(frame: pd.DataFrame | None, value: str, key: str = "channel_id") -> dict:
    """key -> value dict from a reference frame; {} when either column is absent."""
    if frame is None or frame.empty or key not in frame.columns or value not in frame.columns:
        return {}
    return frame.set_index(key)[value].to_dict()


def _mapped(series: pd.Series, mapping: dict, fill: str = "") -> pd.Series:
    """Vectorised dict lookup; missing keys become fill."""
    return series.astype(str).map(mapping).fillna(fill).astype("string")


def transform_channels(
    df: pd.DataFrame,
    *,
    artists: pd.DataFrame,
    run_id: str,
    extracted_at: datetime,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    channel_ids = df["channel_id"]
    out = pd.DataFrame(
        {
            "channel_id": _str(channel_ids),
            "artist_name": _mapped(channel_ids, _lookup(artists, "artist_name")),
            "channel_title": _str(df["channel_title"]),
            "chart_rank": _int(channel_ids.astype(str).map(_lookup(artists, "rank"))),
            "chart_week": _mapped(channel_ids, _lookup(artists, "chart_week")),
            "published_at": _as_ts(df["published_at"]),
            "subscriber_count": _int(df["subscriber_count"]),
            "view_count": _int(df["view_count"]),
            "video_count": _int(df["video_count"]),
            "uploads_playlist_id": _str(df["uploads_playlist_id"]),
        }
    )
    return _with_lineage(out, run_id, extracted_at)


def transform_snapshot(
    channels: pd.DataFrame, *, run_id: str, extracted_at: datetime
) -> pd.DataFrame:
    if channels is None or channels.empty:
        return pd.DataFrame()
    ts = extracted_at.astimezone(timezone.utc)
    return pd.DataFrame(
        {
            "channel_id": channels["channel_id"],
            "snapshot_date": ts.date(),
            "artist_name": channels["artist_name"],
            "subscriber_count": channels["subscriber_count"],
            "channel_view_count": channels["view_count"],
            "video_count": channels["video_count"],
            "extracted_at": ts,
            "run_id": run_id,
        }
    )


def transform_videos(
    df: pd.DataFrame,
    *,
    artists: pd.DataFrame,
    run_id: str,
    extracted_at: datetime,
    channels: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    channel_ids = df["channel_id"]
    out = pd.DataFrame(
        {
            "video_id": _str(df["video_id"]),
            "channel_id": _str(channel_ids),
            "artist_name": _mapped(channel_ids, _lookup(artists, "artist_name")),
            "channel_title": _mapped(channel_ids, _lookup(channels, "channel_title")),
            "video_title": _str(df["video_title"]),
            "description": _str(df["description"]),
            "published_at": _as_ts(df["published_at"]),
            "duration": _str(df["duration"]),
            "tags": _str(df["tags"]),
            "category_id": _str(df["category_id"]),
            "view_count": _int(df["view_count"]),
            "like_count": _int(df["like_count"]),
            "comment_count": _int(df["comment_count"]),
        }
    )
    return _with_lineage(out, run_id, extracted_at)


def transform_comments(
    df: pd.DataFrame,
    *,
    videos: pd.DataFrame,
    artists: pd.DataFrame,
    run_id: str,
    extracted_at: datetime,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    video_channel = {
        str(k): str(v) for k, v in _lookup(videos, "channel_id", key="video_id").items()
    }
    channel_ids = df["video_id"].astype(str).map(video_channel).fillna("")
    out = pd.DataFrame(
        {
            "comment_id": _str(df["comment_id"]),
            "video_id": _str(df["video_id"]),
            "channel_id": _str(channel_ids),
            "artist_name": _mapped(channel_ids, _lookup(artists, "artist_name")),
            "parent_id": _str(df["parent_id"]),
            "author_name": _str(df["author_name"]),
            "comment_text": _str(df["comment_text"]),
            "published_at": _as_ts(df["published_at"]),
            "updated_at": _as_ts(df["updated_at"]),
            "like_count": _int(df["like_count"]),
            "reply_count": _int(df["reply_count"]),
        }
    )
    return _with_lineage(out, run_id, extracted_at)
