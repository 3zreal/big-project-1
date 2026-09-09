"""Shared helpers: env, logging, paths, BigQuery client, registry CSV, raw JSON."""
import csv
import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_RAW_DIR = ROOT_DIR / "data" / "raw"
DATA_PROCESSED_DIR = ROOT_DIR / "data" / "processed"
LOGS_DIR = ROOT_DIR / "logs"
REGISTRY_PATH = DATA_PROCESSED_DIR / "artists_registry.csv"


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


@lru_cache(maxsize=1)
def get_bq_client():
    """Create (once per process) a BigQuery client from GOOGLE_APPLICATION_CREDENTIALS."""
    from google.cloud import bigquery

    project = os.getenv("GCP_PROJECT_ID")
    return bigquery.Client(project=project) if project else bigquery.Client()


def chunked(values: list[str], size: int):
    """Yield successive size-length slices of values."""
    for i in range(0, len(values), size):
        yield values[i : i + size]


def read_registry_rows(limit: int | None = None) -> list[dict[str, str]]:
    """Freeze-CSV rows that have a resolved UC channel_id, in chart order."""
    if not REGISTRY_PATH.exists():
        raise SystemExit(
            f"Missing {REGISTRY_PATH}. Run scripts/fetch_artist_100.py then resolve_channel_ids.py"
        )
    rows: list[dict[str, str]] = []
    with REGISTRY_PATH.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if not (row.get("channel_id") or "").strip().startswith("UC"):
                continue
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
    if not rows:
        raise SystemExit("No UC channel_id in the freeze CSV")
    return rows


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def raw_run_dir(run_id: str) -> Path:
    """data/raw/{run_id}/ for one fetch pass."""
    path = DATA_RAW_DIR / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))
