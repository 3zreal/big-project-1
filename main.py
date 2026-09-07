"""Pipeline orchestrator: fetch -> transform -> load.

Run:
    python main.py

Copy .env.example to .env and set GOOGLE_APPLICATION_CREDENTIALS before running.

Phase 3: load(..., *, write_disposition=) has no default. Curated tables MERGE
from stg_* (never WRITE_TRUNCATE). Full run() wiring is phase 4.
"""
from etl.load import land_and_merge, load_staging, merge_from_staging
from etl.utils import load_env, require_env, setup_logging


def run() -> None:
    """Run one YouTube → BigQuery pipeline pass."""
    load_env()
    logger = setup_logging()
    require_env("GCP_PROJECT_ID")

    logger.info("Starting YouTube pipeline")
    logger.info(
        "Phase 4 wires freeze CSV → fetch → %s / %s / %s (no curated TRUNCATE)",
        land_and_merge.__name__,
        load_staging.__name__,
        merge_from_staging.__name__,
    )
    logger.info("Pipeline finished")


if __name__ == "__main__":
    run()
