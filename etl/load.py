"""LOAD — write a DataFrame to BigQuery.

table_id has three parts: "project.dataset.table".
"""
import logging

from google.cloud import bigquery

logger = logging.getLogger(__name__)


def load(df, table_id: str, write_disposition: str = "WRITE_TRUNCATE"):
    """Load a DataFrame into a BigQuery table.

    write_disposition:
      - WRITE_TRUNCATE: wipe then rewrite (full load — easy level).
      - WRITE_APPEND: append rows.
    """
    client = bigquery.Client()
    job_config = bigquery.LoadJobConfig(write_disposition=write_disposition)
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()
    logger.info("[load] Loaded %s rows into %s (%s)", len(df), table_id, write_disposition)
