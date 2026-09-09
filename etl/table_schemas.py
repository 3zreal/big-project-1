"""Column definitions for every table. The single source of truth for shape.

`transform` builds frames with these names, `load` aligns to them and `schema`
creates the tables from them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from google.cloud import bigquery

from etl.config import Tables

S = bigquery.SchemaField


def _s(*specs: tuple[str, str]) -> list[bigquery.SchemaField]:
    return [S(name, typ) for name, typ in specs]


def columns(schema: list[bigquery.SchemaField]) -> tuple[str, ...]:
    """Column names of a schema, in table order."""
    return tuple(f.name for f in schema)


# Every extract-grain table carries the same provenance tail; transform._lineage
# produces exactly these keys.
LINEAGE = _s(
    ("extracted_at", "TIMESTAMP"),
    ("ingestion_date", "DATE"),
    ("source", "STRING"),
    ("run_id", "STRING"),
)

VIDEO_SCHEMA = _s(
    ("video_id", "STRING"),
    ("channel_id", "STRING"),
    ("artist_name", "STRING"),
    ("channel_title", "STRING"),
    ("video_title", "STRING"),
    ("description", "STRING"),
    ("published_at", "TIMESTAMP"),
    ("duration", "STRING"),
    ("tags", "STRING"),
    ("category_id", "STRING"),
    ("view_count", "INT64"),
    ("like_count", "INT64"),
    ("comment_count", "INT64"),
) + LINEAGE

COMMENT_SCHEMA = _s(
    ("comment_id", "STRING"),
    ("video_id", "STRING"),
    ("channel_id", "STRING"),
    ("artist_name", "STRING"),
    ("parent_id", "STRING"),
    ("author_name", "STRING"),
    ("comment_text", "STRING"),
    ("published_at", "TIMESTAMP"),
    ("updated_at", "TIMESTAMP"),
    ("like_count", "INT64"),
    ("reply_count", "INT64"),
) + LINEAGE

CHANNEL_SCHEMA = _s(
    ("channel_id", "STRING"),
    ("artist_name", "STRING"),
    ("channel_title", "STRING"),
    ("chart_rank", "INT64"),
    ("chart_week", "STRING"),
    ("published_at", "TIMESTAMP"),
    ("subscriber_count", "INT64"),
    ("view_count", "INT64"),
    ("video_count", "INT64"),
    ("uploads_playlist_id", "STRING"),
) + LINEAGE

SNAPSHOT_SCHEMA = _s(
    ("channel_id", "STRING"),
    ("snapshot_date", "DATE"),
    ("artist_name", "STRING"),
    ("subscriber_count", "INT64"),
    ("channel_view_count", "INT64"),
    ("video_count", "INT64"),
    ("extracted_at", "TIMESTAMP"),
    ("run_id", "STRING"),
)

RUN_SCHEMA = _s(
    ("run_id", "STRING"),
    ("started_at", "TIMESTAMP"),
    ("finished_at", "TIMESTAMP"),
    ("status", "STRING"),
    ("units_spent", "INT64"),
    ("pacific_date", "STRING"),
    ("channel_count", "INT64"),
    ("video_count", "INT64"),
    ("comment_count", "INT64"),
    ("error_message", "STRING"),
)

CHECKPOINT_SCHEMA = _s(
    ("checkpoint_key", "STRING"),
    ("comment_pending_video_ids", "STRING"),
    ("completed_video_ids", "STRING"),
    ("units_spent", "INT64"),
    ("pacific_date", "STRING"),
    ("watermarks", "STRING"),
    ("updated_at", "STRING"),
)

CHECKPOINT_COLUMNS = columns(CHECKPOINT_SCHEMA)
CHECKPOINT_KEY_COLUMN = "checkpoint_key"


@dataclass(frozen=True)
class TableSpec:
    """One table to create: what it looks like and how it is laid out."""

    table_id: str
    schema: list[bigquery.SchemaField]
    partition_field: str | None = None
    cluster: tuple[str, ...] = field(default_factory=tuple)


def table_specs(t: Tables) -> list[TableSpec]:
    """Every table the pipeline writes, in creation order."""
    by_channel = ("channel_id",)
    return [
        TableSpec(t.raw_videos, VIDEO_SCHEMA, "ingestion_date", by_channel),
        TableSpec(t.raw_comments, COMMENT_SCHEMA, "ingestion_date", by_channel),
        TableSpec(t.raw_channels, CHANNEL_SCHEMA, "ingestion_date", by_channel),
        TableSpec(t.stg_videos, VIDEO_SCHEMA),
        TableSpec(t.stg_comments, COMMENT_SCHEMA),
        TableSpec(t.stg_channels, CHANNEL_SCHEMA),
        TableSpec(t.stg_snapshot, SNAPSHOT_SCHEMA),
        TableSpec(t.videos, VIDEO_SCHEMA, cluster=by_channel),
        TableSpec(t.comments, COMMENT_SCHEMA, cluster=by_channel),
        TableSpec(t.artists, CHANNEL_SCHEMA, cluster=by_channel),
        TableSpec(t.channel_daily_snapshot, SNAPSHOT_SCHEMA, "snapshot_date", by_channel),
        TableSpec(t.pipeline_runs, RUN_SCHEMA),
        TableSpec(t.fetch_checkpoint, CHECKPOINT_SCHEMA),
    ]
