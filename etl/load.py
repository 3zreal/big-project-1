"""LOAD — BigQuery landing, staging TRUNCATE, curated MERGE.

WRITE_TRUNCATE is allowed only on tables whose name starts with stg_.
Curated upserts use SQL MERGE, never load_table_from_dataframe as an upsert.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

import pandas as pd
from google.cloud import bigquery

from etl.utils import get_bq_client
from etl.checkpoint import checkpoint_row
from etl.table_schemas import CHECKPOINT_COLUMNS, CHECKPOINT_KEY_COLUMN

logger = logging.getLogger(__name__)

_TRUNCATE = "WRITE_TRUNCATE"
_APPEND = "WRITE_APPEND"


def _table_name(table_id: str) -> str:
    return table_id.rsplit(".", 1)[-1]


def load(df: pd.DataFrame, table_id: str, *, write_disposition: str) -> None:
    """Load a DataFrame. write_disposition is required (no default)."""
    if write_disposition == _TRUNCATE and not _table_name(table_id).startswith("stg_"):
        raise ValueError(f"WRITE_TRUNCATE is only allowed on stg_* tables, not {table_id}")
    if df is None or df.empty:
        logger.info("[load] skip empty frame for %s", table_id)
        return
    client = get_bq_client()
    job_config = bigquery.LoadJobConfig(write_disposition=write_disposition)
    try:
        schema = client.get_table(table_id).schema
        job_config.schema = schema
    except Exception:
        logger.warning("[load] no schema for %s; loading unaligned", table_id)
        schema = None
    aligned = _align_frame(df, schema, table_id)
    job = client.load_table_from_dataframe(aligned, table_id, job_config=job_config)
    job.result()
    logger.info("[load] %s rows -> %s (%s)", len(aligned), table_id, write_disposition)


def _align_frame(
    df: pd.DataFrame, schema: list[bigquery.SchemaField] | None, table_id: str = ""
) -> pd.DataFrame:
    """Keep table columns only; coerce DATE / TIMESTAMP / INT64 for pyarrow."""
    if schema is None:
        return df
    out = df.copy()
    names = [field.name for field in schema]
    dropped = [c for c in out.columns if c not in names]
    if dropped:
        # Loud, because a silent drop here is data loss with a green run status.
        logger.warning("[load] %s columns not in schema, dropped: %s", table_id, dropped)
    for field in schema:
        if field.name not in out.columns:
            out[field.name] = None
        col = out[field.name]
        ftype = field.field_type
        if ftype == "DATE":
            out[field.name] = pd.to_datetime(col, utc=True, errors="coerce").dt.date
        elif ftype == "TIMESTAMP":
            out[field.name] = pd.to_datetime(col, utc=True, errors="coerce")
        elif ftype in {"INTEGER", "INT64"}:
            out[field.name] = pd.to_numeric(col, errors="coerce").fillna(0).astype("int64")
        elif ftype == "STRING":
            out[field.name] = col.fillna("").astype(str)
    return out[names]


def load_staging(df: pd.DataFrame, stg_table: str, key: Sequence[str]) -> None:
    """Dedupe on PK, then WRITE_TRUNCATE the staging table."""
    if not _table_name(stg_table).startswith("stg_"):
        raise ValueError(f"load_staging target must be stg_*, got {stg_table}")
    keys = [k for k in key if k in df.columns]
    staged = df.drop_duplicates(subset=keys, keep="last") if keys else df
    load(staged, stg_table, write_disposition=_TRUNCATE)


def append_raw(df: pd.DataFrame, table_id: str) -> None:
    """APPEND-only landing. Never truncates raw_* or curated facts."""
    if _table_name(table_id).startswith("stg_"):
        raise ValueError(f"append_raw cannot target staging {table_id}")
    load(df, table_id, write_disposition=_APPEND)


def _merge_sql(dest_id: str, source_sql: str, keys: Sequence[str], columns: Sequence[str]) -> str:
    """MERGE dest from any source relation. No WHEN NOT MATCHED BY SOURCE THEN DELETE."""
    on_sql = " AND ".join(f"T.`{k}` = S.`{k}`" for k in keys)
    non_keys = [c for c in columns if c not in keys]
    if non_keys:
        update_sql = ", ".join(f"T.`{c}` = S.`{c}`" for c in non_keys)
    else:
        update_sql = f"T.`{keys[0]}` = S.`{keys[0]}`"
    insert_cols = ", ".join(f"`{c}`" for c in columns)
    insert_vals = ", ".join(f"S.`{c}`" for c in columns)
    return f"""
    MERGE `{dest_id}` T
    USING {source_sql} S
    ON {on_sql}
    WHEN MATCHED THEN UPDATE SET {update_sql}
    WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})
    """


def merge_from_staging(
    stg_id: str, dest_id: str, key: Sequence[str], columns: Sequence[str]
) -> None:
    """SQL MERGE dest from staging."""
    cols = [c for c in columns if c]
    if not cols:
        logger.info("[merge] no columns for %s, skip", dest_id)
        return
    sql = _merge_sql(dest_id, f"`{stg_id}`", key, cols)
    get_bq_client().query(sql).result()
    logger.info("[merge] %s <- %s on %s", dest_id, stg_id, ",".join(key))


def land_and_merge(
    df: pd.DataFrame,
    *,
    raw_table: str,
    stg_table: str,
    dest_table: str,
    key: Sequence[str],
) -> None:
    """APPEND raw → TRUNCATE stg_* → MERGE curated. One extract grain."""
    if df is None or df.empty:
        logger.info("[land] skip empty %s", dest_table)
        return
    append_raw(df, raw_table)
    load_staging(df, stg_table, key)
    merge_from_staging(stg_table, dest_table, key, list(df.columns))


def persist_checkpoint_bq(state: dict, table_id: str) -> None:
    """MERGE the single checkpoint row, parameterized. Local JSON is saved by the caller."""
    row = checkpoint_row(state)
    select_sql = "(SELECT " + ", ".join(f"@{c} AS {c}" for c in CHECKPOINT_COLUMNS) + ")"
    sql = _merge_sql(table_id, select_sql, (CHECKPOINT_KEY_COLUMN,), CHECKPOINT_COLUMNS)
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter(
                name,
                "INT64" if isinstance(row[name], int) else "STRING",
                row[name],
            )
            for name in CHECKPOINT_COLUMNS
        ]
    )
    get_bq_client().query(sql, job_config=job_config).result()
    logger.info("[checkpoint] MERGE %s pending=%s", table_id, row["comment_pending_video_ids"])
