"""test_connect.py — check that the environment can reach BigQuery.

A successful run that prints ok = 1 means the setup is ready.
"""
from etl.utils import get_bq_client
from etl.utils import load_env

load_env()
client = get_bq_client()
df = client.query("SELECT 1 AS ok").to_dataframe()
print(df)
