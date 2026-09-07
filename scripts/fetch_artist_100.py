"""One-off freeze: Billboard Artist 100 → data/processed/artists_registry.csv.

Not part of the YouTube cron path. Re-runs keep channel fields when the
artist name is unchanged. Fill IDs with scripts/resolve_channel_ids.py.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.billboard import fetch_artist_100

OUT_PATH = ROOT / "data" / "processed" / "artists_registry.csv"
COLUMNS = (
    "rank",
    "artist_name",
    "chart_week",
    "accessed_at",
    "channel_id",
    "channel_title",
    "id_resolution_method",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze Billboard Artist 100 to CSV")
    parser.add_argument(
        "--date",
        default=None,
        help="Chart week YYYY-MM-DD (default: latest)",
    )
    args = parser.parse_args()

    chart_week, entries = fetch_artist_100(args.date)
    accessed_at = datetime.now(timezone.utc).date().isoformat()

    previous: dict[str, dict[str, str]] = {}
    if OUT_PATH.exists():
        with OUT_PATH.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                previous[row.get("artist_name", "")] = row

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for entry in entries:
            prior = previous.get(entry.artist_name, {})
            writer.writerow(
                {
                    "rank": entry.rank,
                    "artist_name": entry.artist_name,
                    "chart_week": chart_week,
                    "accessed_at": accessed_at,
                    "channel_id": prior.get("channel_id", ""),
                    "channel_title": prior.get("channel_title", ""),
                    "id_resolution_method": prior.get("id_resolution_method", ""),
                }
            )

    print(f"Wrote {len(entries)} rows to {OUT_PATH} (week {chart_week})")


if __name__ == "__main__":
    main()
