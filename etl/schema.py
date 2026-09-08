"""Create missing tables inside existing datasets. Never create datasets."""
from __future__ import annotations

import logging

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from etl.config import dataset_curated, dataset_raw, project_id, tables
from etl.utils import get_bq_client

logger = logging.getLogger(__name__)

S = bigquery.SchemaField


def _s(*fields: tuple[str, str]) -> list[bigquery.SchemaField]:
    return [S(name, typ) for name, typ in fields]


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
    ("extracted_at", "TIMESTAMP"),
    ("ingestion_date", "DATE"),
    ("source", "STRING"),
    ("run_id", "STRING"),
)

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
    ("extracted_at", "TIMESTAMP"),
    ("ingestion_date", "DATE"),
    ("source", "STRING"),
    ("run_id", "STRING"),
)

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
    ("extracted_at", "TIMESTAMP"),
    ("ingestion_date", "DATE"),
    ("source", "STRING"),
    ("run_id", "STRING"),
)

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


def ensure_tables() -> dict[str, str]:
    """Fail if datasets are missing. Create tables that do not exist yet."""
    client = get_bq_client()
    project = project_id()
    for ds_name in (dataset_raw(), dataset_curated()):
        fq = f"{project}.{ds_name}"
        try:
            client.get_dataset(fq)
        except NotFound as err:
            raise RuntimeError(
                f"Dataset {fq} not found. Create it in the BigQuery console "
                "(code does not create datasets)."
            ) from err

    ids = tables()
    specs: list[tuple[str, list[bigquery.SchemaField], str | None, list[str] | None]] = [
        (ids["raw_videos"], VIDEO_SCHEMA, "ingestion_date", ["channel_id"]),
        (ids["raw_comments"], COMMENT_SCHEMA, "ingestion_date", ["channel_id"]),
        (ids["raw_channels"], CHANNEL_SCHEMA, "ingestion_date", ["channel_id"]),
        (ids["stg_videos"], VIDEO_SCHEMA, None, None),
        (ids["stg_comments"], COMMENT_SCHEMA, None, None),
        (ids["stg_channels"], CHANNEL_SCHEMA, None, None),
        (ids["stg_snapshot"], SNAPSHOT_SCHEMA, None, None),
        (ids["videos"], VIDEO_SCHEMA, None, ["channel_id"]),
        (ids["comments"], COMMENT_SCHEMA, None, ["channel_id"]),
        (ids["artists"], CHANNEL_SCHEMA, None, ["channel_id"]),
        (ids["channel_daily_snapshot"], SNAPSHOT_SCHEMA, "snapshot_date", ["channel_id"]),
        (ids["pipeline_runs"], RUN_SCHEMA, None, None),
        (ids["fetch_checkpoint"], CHECKPOINT_SCHEMA, None, None),
    ]
    for table_id, schema, partition_field, cluster in specs:
        _ensure_table(client, table_id, schema, partition_field, cluster)
    return ids


def _ensure_table(
    client: bigquery.Client,
    table_id: str,
    schema: list[bigquery.SchemaField],
    partition_field: str | None,
    cluster: list[str] | None,
) -> None:
    try:
        client.get_table(table_id)
        logger.debug("table exists %s", table_id)
        return
    except NotFound:
        pass
    table = bigquery.Table(table_id, schema=schema)
    if partition_field:
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=partition_field,
        )
    if cluster:
        table.clustering_fields = cluster
    client.create_table(table)
    logger.info("created table %s", table_id)
