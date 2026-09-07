"""TRANSFORM — dtypes, lineage, assignment field names.

Called after fetch, before staging load. tags stay STRING.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

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


def _col(df: pd.DataFrame, name: str, fill="") -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series([fill] * len(df))


def transform_channels(
    df: pd.DataFrame,
    *,
    artists: pd.DataFrame,
    run_id: str,
    extracted_at: datetime,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    names = artists.set_index("channel_id")["artist_name"].to_dict() if not artists.empty else {}
    ranks = artists.set_index("channel_id")["rank"].to_dict() if (not artists.empty and "rank" in artists.columns) else {}
    weeks = (
        artists.set_index("channel_id")["chart_week"].to_dict()
        if (not artists.empty and "chart_week" in artists.columns)
        else {}
    )
    out = pd.DataFrame(
        {
            "channel_id": _col(df, "channel_id").fillna("").astype("string"),
            "artist_name": df["channel_id"].map(lambda c: names.get(str(c), "")).fillna("").astype("string"),
            "channel_title": _col(df, "channel_title").fillna("").astype("string"),
            "chart_rank": pd.to_numeric(df["channel_id"].map(lambda c: ranks.get(str(c), 0)), errors="coerce")
            .fillna(0)
            .astype("int64"),
            "chart_week": df["channel_id"].map(lambda c: weeks.get(str(c), "")).fillna("").astype("string"),
            "published_at": _as_ts(_col(df, "published_at")),
            "subscriber_count": _int(_col(df, "subscriber_count", 0)),
            "view_count": _int(_col(df, "view_count", 0)),
            "video_count": _int(_col(df, "video_count", 0)),
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
    names = artists.set_index("channel_id")["artist_name"].to_dict() if not artists.empty else {}
    titles = {}
    if channels is not None and not channels.empty:
        titles = channels.set_index("channel_id")["channel_title"].to_dict()
    title_col = df["video_title"] if "video_title" in df.columns else _col(df, "title")
    out = pd.DataFrame(
        {
            "video_id": _col(df, "video_id").fillna("").astype("string"),
            "channel_id": _col(df, "channel_id").fillna("").astype("string"),
            "artist_name": df["channel_id"].map(lambda c: names.get(str(c), "")).fillna("").astype("string"),
            "channel_title": df["channel_id"].map(lambda c: titles.get(str(c), "")).fillna("").astype("string"),
            "video_title": title_col.fillna("").astype("string"),
            "description": _col(df, "description").fillna("").astype("string"),
            "published_at": _as_ts(_col(df, "published_at")),
            "duration": _col(df, "duration").fillna("").astype("string"),
            "tags": _col(df, "tags").fillna("").astype("string"),
            "category_id": _col(df, "category_id").fillna("").astype("string"),
            "view_count": _int(_col(df, "view_count", 0)),
            "like_count": _int(_col(df, "like_count", 0)),
            "comment_count": _int(_col(df, "comment_count", 0)),
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
    video_channel = {}
    if videos is not None and not videos.empty and "video_id" in videos.columns:
        video_channel = videos.set_index("video_id")["channel_id"].astype(str).to_dict()
    names = artists.set_index("channel_id")["artist_name"].to_dict() if not artists.empty else {}
    author = df["author_name"] if "author_name" in df.columns else _col(df, "author")
    text = df["comment_text"] if "comment_text" in df.columns else _col(df, "text")
    channel_ids = df["video_id"].map(lambda v: str(video_channel.get(str(v), "")))
    updated = _col(df, "updated_at") if "updated_at" in df.columns else _col(df, "published_at")
    out = pd.DataFrame(
        {
            "comment_id": _col(df, "comment_id").fillna("").astype("string"),
            "video_id": _col(df, "video_id").fillna("").astype("string"),
            "channel_id": channel_ids.fillna("").astype("string"),
            "artist_name": channel_ids.map(lambda c: names.get(str(c), "")).fillna("").astype("string"),
            "parent_id": _col(df, "parent_id").fillna("").astype("string"),
            "author_name": author.fillna("").astype("string"),
            "comment_text": text.fillna("").astype("string"),
            "published_at": _as_ts(_col(df, "published_at")),
            "updated_at": _as_ts(updated),
            "like_count": _int(_col(df, "like_count", 0)),
            "reply_count": _int(_col(df, "reply_count", 0)),
        }
    )
    for key, val in _lineage(run_id, extracted_at).items():
        out[key] = val
    out.attrs.update(attrs)
    return out


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ingestion_today() -> date:
    return utc_now().date()
