"""Shared helpers: paths, env, logging, JSON files, sequences, BigQuery client."""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_RAW_DIR = ROOT_DIR / "data" / "raw"
DATA_PROCESSED_DIR = ROOT_DIR / "data" / "processed"
LOGS_DIR = ROOT_DIR / "logs"

# Google's client is chatty at INFO; every entrypoint wants these quiet.
_NOISY_LOGGERS = ("googleapiclient.discovery_cache", "googleapiclient.http")


def load_env() -> None:
    """Load environment variables from the project-root .env file."""
    load_dotenv(ROOT_DIR / ".env")


def require_env(name: str) -> str:
    """Return a required env var, or raise a clear error if missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing environment variable {name}. Copy .env.example to .env and fill in the values."
        )
    return value


def setup_logging(log_file: str | None = None) -> logging.Logger:
    """Log to console and file (default: logs/pipeline.log)."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = LOGS_DIR / (log_file or "pipeline.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(path, encoding="utf-8")],
        force=True,
    )
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.ERROR)
    return logging.getLogger("pipeline")


def bootstrap(
    log_file: str | None = None,
    *,
    require: tuple[str, ...] = (),
    logger_name: str = "pipeline",
) -> logging.Logger:
    """Load .env, configure logging, fail early on missing env vars."""
    load_env()
    setup_logging(log_file)
    for name in require:
        require_env(name)
    return logging.getLogger(logger_name)


@lru_cache(maxsize=1)
def get_bq_client():
    """Create (once per process) a BigQuery client from GOOGLE_APPLICATION_CREDENTIALS."""
    from google.cloud import bigquery

    project = os.getenv("GCP_PROJECT_ID")
    return bigquery.Client(project=project) if project else bigquery.Client()


def chunked(values: list[str], size: int) -> Iterator[list[str]]:
    """Yield successive size-length slices of values."""
    for i in range(0, len(values), size):
        yield values[i : i + size]


def dedupe(values: Iterable[str], *, key: Callable[[str], str] | None = None) -> list[str]:
    """Order-preserving de-duplication that also drops empty strings."""
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        marker = key(value) if key else value
        if value and marker not in seen:
            seen.add(marker)
            unique.append(value)
    return unique


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
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {} if default is None else default
