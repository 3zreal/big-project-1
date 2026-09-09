"""TRANSFORM — dtypes, lineage, assignment field names.

Called after fetch, before staging load. tags stay STRING.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

SOURCE = "youtube_data_api_v3"


def _lineage(run_id: str, extracted_at: datetime) -> dict:
    ts = extracted_at.astimezone(timezone.utc)
    return {
        "extracted_at": ts,
        "ingestion_date": ts.date(),
        "source": SOURCE,
        "run_id": run_id,
    }


def _as_ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0).astype("int64")


def _col(df: pd.DataFrame, *names: str, fill="") -> pd.Series:
    """First present column among names; a fill-valued series when none exist."""
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series([fill] * len(df))


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
    names = _lookup(artists, "artist_name")
    ranks = _lookup(artists, "rank")
    weeks = _lookup(artists, "chart_week")
    out = pd.DataFrame(
        {
            "channel_id": _col(df, "channel_id").fillna("").astype("string"),
            "artist_name": _mapped(df["channel_id"], names),
            "channel_title": _col(df, "channel_title").fillna("").astype("string"),
            "chart_rank": _int(df["channel_id"].astype(str).map(ranks)),
            "chart_week": _mapped(df["channel_id"], weeks),
            "published_at": _as_ts(_col(df, "published_at")),
            "subscriber_count": _int(_col(df, "subscriber_count", fill=0)),
            "view_count": _int(_col(df, "view_count", fill=0)),
            "video_count": _int(_col(df, "video_count", fill=0)),
            "uploads_playlist_id": _col(df, "uploads_playlist_id").fillna("").astype("string"),
        }
    )
    for key, val in _lineage(run_id, extracted_at).items():
        out[key] = val
    return out


def transform_snapshot(channels: pd.DataFrame, *, run_id: str, extracted_at: datetime) -> pd.DataFrame:
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
    """Rename assignment fields, add lineage. Keep fetch attrs (remainder)."""
    remainder = list(df.attrs.get("remainder") or []) if df is not None else []
    if df is None or df.empty:
        empty = pd.DataFrame()
        empty.attrs["remainder"] = remainder
        return empty
    names = _lookup(artists, "artist_name")
    titles = _lookup(channels, "channel_title")
    title_col = _col(df, "video_title", "title")
    out = pd.DataFrame(
        {
            "video_id": _col(df, "video_id").fillna("").astype("string"),
            "channel_id": _col(df, "channel_id").fillna("").astype("string"),
            "artist_name": _mapped(df["channel_id"], names),
            "channel_title": _mapped(df["channel_id"], titles),
            "video_title": title_col.fillna("").astype("string"),
            "description": _col(df, "description").fillna("").astype("string"),
            "published_at": _as_ts(_col(df, "published_at")),
            "duration": _col(df, "duration").fillna("").astype("string"),
            "tags": _col(df, "tags").fillna("").astype("string"),
            "category_id": _col(df, "category_id").fillna("").astype("string"),
            "view_count": _int(_col(df, "view_count", fill=0)),
            "like_count": _int(_col(df, "like_count", fill=0)),
            "comment_count": _int(_col(df, "comment_count", fill=0)),
        }
    )
    for key, val in _lineage(run_id, extracted_at).items():
        out[key] = val
    out.attrs["remainder"] = remainder
    return out


def transform_comments(
    df: pd.DataFrame,
    *,
    videos: pd.DataFrame,
    artists: pd.DataFrame,
    run_id: str,
    extracted_at: datetime,
) -> pd.DataFrame:
    attrs = dict(df.attrs) if df is not None else {}
    if df is None or df.empty:
        empty = pd.DataFrame()
        empty.attrs.update(attrs)
        return empty
    video_channel = _lookup(videos, "channel_id", key="video_id")
    video_channel = {str(k): str(v) for k, v in video_channel.items()}
    names = _lookup(artists, "artist_name")
    author = _col(df, "author_name", "author")
    text = _col(df, "comment_text", "text")
    channel_ids = df["video_id"].astype(str).map(video_channel).fillna("")
    updated = _col(df, "updated_at", "published_at")
    out = pd.DataFrame(
        {
            "comment_id": _col(df, "comment_id").fillna("").astype("string"),
            "video_id": _col(df, "video_id").fillna("").astype("string"),
            "channel_id": channel_ids.fillna("").astype("string"),
            "artist_name": _mapped(channel_ids, names),
            "parent_id": _col(df, "parent_id").fillna("").astype("string"),
            "author_name": author.fillna("").astype("string"),
            "comment_text": text.fillna("").astype("string"),
            "published_at": _as_ts(_col(df, "published_at")),
            "updated_at": _as_ts(updated),
            "like_count": _int(_col(df, "like_count", fill=0)),
            "reply_count": _int(_col(df, "reply_count", fill=0)),
        }
    )
    for key, val in _lineage(run_id, extracted_at).items():
        out[key] = val
    out.attrs.update(attrs)
    return out
