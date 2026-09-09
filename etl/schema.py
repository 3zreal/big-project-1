"""Create missing tables inside existing datasets. Never create datasets."""
from __future__ import annotations

import logging

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from etl.utils import get_bq_client
from etl.config import Tables, dataset_curated, dataset_raw, project_id, tables
from etl.table_schemas import TableSpec, table_specs

logger = logging.getLogger(__name__)


def ensure_tables() -> Tables:
    """Fail if datasets are missing. Create tables that do not exist yet."""
    client = get_bq_client()
    _require_datasets(client)
    ids = tables()
    for spec in table_specs(ids):
        _create_if_missing(client, spec)
    return ids


def _require_datasets(client: bigquery.Client) -> None:
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


def _create_if_missing(client: bigquery.Client, spec: TableSpec) -> None:
    """One idempotent create per table; BigQuery answers 'does it exist' for us."""
    table = bigquery.Table(spec.table_id, schema=spec.schema)
    if spec.partition_field:
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=spec.partition_field,
        )
    if spec.cluster:
        table.clustering_fields = list(spec.cluster)
    client.create_table(table, exists_ok=True)
    logger.debug("table ready %s", spec.table_id)
