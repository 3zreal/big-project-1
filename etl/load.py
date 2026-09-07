"""LOAD — BigQuery landing, staging TRUNCATE, curated MERGE.

WRITE_TRUNCATE is allowed only on tables whose name starts with stg_.
Curated upserts use SQL MERGE, never load_table_from_dataframe as an upsert.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

import pandas as pd
from google.cloud import bigquery

from etl.checkpoint import checkpoint_row, save_checkpoint
from etl.utils import get_bq_client

logger = logging.getLogger(__name__)

_TRUNCATE = "WRITE_TRUNCATE"
_APPEND = "WRITE_APPEND"


def _table_name(table_id: str) -> str:
    return table_id.rsplit(".", 1)[-1]


def _keys(key: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(key, str):
        return (key,)
    return tuple(key)


def load(df: pd.DataFrame, table_id: str, *, write_disposition: str) -> None:
    """Load a DataFrame. write_disposition is required (no default)."""
    if write_disposition == _TRUNCATE and not _table_name(table_id).startswith("stg_"):
        raise ValueError(
            f"WRITE_TRUNCATE is only allowed on stg_* tables, not {table_id}"
        )
    if df is None or df.empty:
        logger.info("[load] skip empty frame for %s", table_id)
        return
    client = get_bq_client()
    aligned = _align_frame(df, table_id, client)
    job_config = bigquery.LoadJobConfig(write_disposition=write_disposition)
    try:
        table = client.get_table(table_id)
        job_config.schema = table.schema
    except Exception:
        pass
    job = client.load_table_from_dataframe(aligned, table_id, job_config=job_config)
    job.result()
    logger.info("[load] %s rows -> %s (%s)", len(aligned), table_id, write_disposition)


def _align_frame(df: pd.DataFrame, table_id: str, client: bigquery.Client) -> pd.DataFrame:
    """Keep table columns only; coerce DATE / TIMESTAMP / INT64 for pyarrow."""
    try:
        schema = client.get_table(table_id).schema
    except Exception:
        return df
    out = df.copy()
    names = [field.name for field in schema]
    for field in schema:
        if field.name not in out.columns:
            out[field.name] = None
        col = out[field.name]
        ftype = field.field_type
        if ftype == "DATE":
            parsed = pd.to_datetime(col, utc=True, errors="coerce")
            out[field.name] = parsed.dt.date
        elif ftype == "TIMESTAMP":
            out[field.name] = pd.to_datetime(col, utc=True, errors="coerce")
        elif ftype in {"INTEGER", "INT64"}:
            out[field.name] = pd.to_numeric(col, errors="coerce").fillna(0).astype("int64")
        elif ftype == "STRING":
            out[field.name] = col.fillna("").astype(str)
    return out[names]


def load_staging(df: pd.DataFrame, stg_table: str, key: str | Sequence[str]) -> None:
    """Dedupe on PK, then WRITE_TRUNCATE the staging table."""
    if not _table_name(stg_table).startswith("stg_"):
        raise ValueError(f"load_staging target must be stg_*, got {stg_table}")
    keys = [k for k in _keys(key) if k in df.columns]
    staged = df.drop_duplicates(subset=keys, keep="last") if keys else df
    load(staged, stg_table, write_disposition=_TRUNCATE)


def append_raw(df: pd.DataFrame, table_id: str) -> None:
    """APPEND-only landing. Never truncates raw_* or curated facts."""
    name = _table_name(table_id)
    if name.startswith("stg_"):
        raise ValueError(f"append_raw cannot target staging {table_id}")
    load(df, table_id, write_disposition=_APPEND)


def merge_from_staging(
    stg_id: str,
    dest_id: str,
    key: str | Sequence[str],
    columns: Sequence[str],
) -> None:
    """SQL MERGE dest from staging. No WHEN NOT MATCHED BY SOURCE THEN DELETE."""
    keys = _keys(key)
    cols = [c for c in columns if c]
    if not cols:
        logger.info("[merge] no columns for %s, skip", dest_id)
        return
    on_sql = " AND ".join(f"T.`{k}` = S.`{k}`" for k in keys)
    non_keys = [c for c in cols if c not in keys]
    if non_keys:
        update_sql = ", ".join(f"T.`{c}` = S.`{c}`" for c in non_keys)
    else:
        update_sql = f"T.`{keys[0]}` = S.`{keys[0]}`"
    insert_cols = ", ".join(f"`{c}`" for c in cols)
    insert_vals = ", ".join(f"S.`{c}`" for c in cols)
    sql = f"""
    MERGE `{dest_id}` T
    USING `{stg_id}` S
    ON {on_sql}
    WHEN MATCHED THEN UPDATE SET {update_sql}
    WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
    """
    client = get_bq_client()
    client.query(sql).result()
    logger.info("[merge] %s <- %s on %s", dest_id, stg_id, ",".join(keys))


def merge_snapshot(stg_id: str, dest_id: str, columns: Sequence[str]) -> None:
    """Upsert channel_daily_snapshot on (channel_id, snapshot_date). Same-day re-run updates."""
    merge_from_staging(stg_id, dest_id, ("channel_id", "snapshot_date"), columns)


def land_and_merge(
    df: pd.DataFrame,
    *,
    raw_table: str,
    stg_table: str,
    dest_table: str,
    key: str | Sequence[str],
) -> None:
    """APPEND raw → TRUNCATE stg_* → MERGE curated. One extract grain."""
    if df is None or df.empty:
        logger.info("[land] skip empty %s", dest_table)
        return
    append_raw(df, raw_table)
    load_staging(df, stg_table, key)
    merge_from_staging(stg_table, dest_table, key, list(df.columns))


def land_and_merge_snapshot(
    df: pd.DataFrame,
    *,
    raw_table: str,
    stg_table: str,
    dest_table: str,
) -> None:
    """APPEND raw → TRUNCATE stg_* → MERGE snapshot on (channel_id, snapshot_date)."""
    if df is None or df.empty:
        logger.info("[land] skip empty snapshot %s", dest_table)
        return
    key = ("channel_id", "snapshot_date")
    append_raw(df, raw_table)
    load_staging(df, stg_table, key)
    merge_snapshot(stg_table, dest_table, list(df.columns))


def persist_checkpoint_bq(state: dict, table_id: str) -> None:
    """Local JSON plus MERGE youtube_curated.fetch_checkpoint (one row, parameterized)."""
    save_checkpoint(state)
    row = checkpoint_row(state)
    sql = f"""
    MERGE `{table_id}` T
    USING (
      SELECT
        @checkpoint_key AS checkpoint_key,
        @pending AS comment_pending_video_ids,
        @completed AS completed_video_ids,
        @units_spent AS units_spent,
        @pacific_date AS pacific_date,
        @watermarks AS watermarks,
        @updated_at AS updated_at
    ) S
    ON T.checkpoint_key = S.checkpoint_key
    WHEN MATCHED THEN UPDATE SET
      T.comment_pending_video_ids = S.comment_pending_video_ids,
      T.completed_video_ids = S.completed_video_ids,
      T.units_spent = S.units_spent,
      T.pacific_date = S.pacific_date,
      T.watermarks = S.watermarks,
      T.updated_at = S.updated_at
    WHEN NOT MATCHED THEN INSERT (
      checkpoint_key, comment_pending_video_ids, completed_video_ids,
      units_spent, pacific_date, watermarks, updated_at
    ) VALUES (
      S.checkpoint_key, S.comment_pending_video_ids, S.completed_video_ids,
      S.units_spent, S.pacific_date, S.watermarks, S.updated_at
    )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("checkpoint_key", "STRING", row["checkpoint_key"]),
            bigquery.ScalarQueryParameter("pending", "STRING", row["comment_pending_video_ids"]),
            bigquery.ScalarQueryParameter("completed", "STRING", row["completed_video_ids"]),
            bigquery.ScalarQueryParameter("units_spent", "INT64", row["units_spent"]),
            bigquery.ScalarQueryParameter("pacific_date", "STRING", row["pacific_date"]),
            bigquery.ScalarQueryParameter("watermarks", "STRING", row["watermarks"]),
            bigquery.ScalarQueryParameter("updated_at", "STRING", row["updated_at"]),
        ]
    )
    client = get_bq_client()
    client.query(sql, job_config=job_config).result()
    logger.info("[checkpoint] MERGE %s pending=%s", table_id, row["comment_pending_video_ids"])
