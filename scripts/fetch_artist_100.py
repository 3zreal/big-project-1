"""One-off freeze: Billboard Artist 100 → data/processed/artists_registry.csv.

Not part of the YouTube cron path. Re-runs keep channel fields when the
artist name is unchanged. Fill IDs with scripts/resolve_channel_ids.py.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from etl.artist_registry import REGISTRY_PATH, read_registry, write_registry

from scripts.billboard import fetch_artist_100


def _existing_by_artist() -> dict[str, dict[str, str]]:
    """Already-resolved channel fields, keyed by artist name."""
    if not REGISTRY_PATH.exists():
        return {}
    _, rows = read_registry()
    return {row.get("artist_name", ""): row for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze Billboard Artist 100 to CSV")
    parser.add_argument("--date", default=None, help="Chart week YYYY-MM-DD (default: latest)")
    args = parser.parse_args()

    chart_week, entries = fetch_artist_100(args.date)
    accessed_at = datetime.now(timezone.utc).date().isoformat()
    previous = _existing_by_artist()

    rows = []
    for entry in entries:
        prior = previous.get(entry.artist_name, {})
        rows.append(
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
    write_registry(rows)
    print(f"Wrote {len(rows)} rows to {REGISTRY_PATH} (week {chart_week})")


if __name__ == "__main__":
    main()
