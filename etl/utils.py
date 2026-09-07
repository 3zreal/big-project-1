"""Shared helpers: env, logging, paths, BigQuery client."""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_RAW_DIR = ROOT_DIR / "data" / "raw"
DATA_PROCESSED_DIR = ROOT_DIR / "data" / "processed"
LOGS_DIR = ROOT_DIR / "logs"


def load_env() -> None:
    """Load environment variables from the project-root .env file."""
    load_dotenv(ROOT_DIR / ".env")


def setup_logging(log_file: str | None = None) -> logging.Logger:
    """Log to console and file (default: logs/pipeline.log)."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = LOGS_DIR / (log_file or "pipeline.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(path, encoding="utf-8"),
        ],
        force=True,
    )
    return logging.getLogger("pipeline")


def require_env(name: str) -> str:
    """Return a required env var, or raise a clear error if missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable {name}. Copy .env.example to .env and fill in the values."
        )
    return value


def get_bq_client():
    """Create a BigQuery client from GOOGLE_APPLICATION_CREDENTIALS."""
    from google.cloud import bigquery

    project = os.getenv("GCP_PROJECT_ID")
    return bigquery.Client(project=project) if project else bigquery.Client()
