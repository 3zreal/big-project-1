"""The frozen Billboard cohort CSV: one path, one reader, one writer."""
from __future__ import annotations

import csv

from etl.utils import DATA_PROCESSED_DIR

REGISTRY_PATH = DATA_PROCESSED_DIR / "artists_registry.csv"

# Header written by the Billboard freeze and preserved by the channel resolver.
REGISTRY_COLUMNS = (
    "rank",
    "artist_name",
    "chart_week",
    "accessed_at",
    "channel_id",
    "channel_title",
    "id_resolution_method",
)


def read_registry() -> tuple[list[str], list[dict[str, str]]]:
    """Every row of the freeze CSV, plus the header actually on disk."""
    if not REGISTRY_PATH.exists():
        raise SystemExit(
            f"Missing {REGISTRY_PATH}. Run scripts/fetch_artist_100.py then resolve_channel_ids.py"
        )
    with REGISTRY_PATH.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or REGISTRY_COLUMNS)
        return fieldnames, list(reader)


def write_registry(rows: list[dict[str, str]], fieldnames: list[str] | None = None) -> None:
    """Rewrite the freeze CSV. Defaults to the canonical column order."""
    names = list(fieldnames or REGISTRY_COLUMNS)
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRY_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=names)
        writer.writeheader()
        writer.writerows(rows)


def read_registry_rows(limit: int | None = None) -> list[dict[str, str]]:
    """Freeze-CSV rows that have a resolved UC channel_id, in chart order."""
    _, all_rows = read_registry()
    rows: list[dict[str, str]] = []
    for row in all_rows:
        if not (row.get("channel_id") or "").strip().startswith("UC"):
            continue
        rows.append(row)
        if limit is not None and len(rows) >= limit:
            break
    if not rows:
        raise SystemExit("No UC channel_id in the freeze CSV")
    return rows
