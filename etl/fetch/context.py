"""One FetchContext per run: API client, quota budget, run directory, playlist cache."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from etl.quota import QuotaBudget
from etl.utils import (
    DATA_PROCESSED_DIR,
    load_env,
    load_json,
    new_run_id,
    raw_run_dir,
    require_env,
    write_json,
)
from etl.youtube_api import build_youtube

logger = logging.getLogger(__name__)

# channel_id -> uploads playlist ID (UU...), so later runs skip a channels.list call.
PLAYLIST_CACHE = DATA_PROCESSED_DIR / "uploads_playlists.json"


def load_playlist_cache() -> dict[str, str]:
    raw = load_json(PLAYLIST_CACHE, default={})
    return {str(k): str(v) for k, v in raw.items() if str(v).startswith("UU")}


def save_playlist_cache(mapping: dict[str, str]) -> None:
    write_json(PLAYLIST_CACHE, mapping)


def uploads_id(item: dict) -> str:
    """contentDetails.relatedPlaylists.uploads, or '' when absent."""
    return item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads") or ""


@dataclass
class FetchContext:
    client: Any
    quota: QuotaBudget
    run_id: str
    run_dir: Path
    playlist_ids: dict[str, str] = field(default_factory=dict)
    # Channels already sent to channels.list this run, so a later phase does not
    # pay to re-ask about IDs that already came back invalid.
    attempted_channels: set[str] = field(default_factory=set)

    def dump_quota(self) -> None:
        """Refresh the run-scoped quota artifact after an API phase."""
        write_json(self.run_dir / "quota.json", self.quota.snapshot())


def start_run(*, daily_limit: int | None = None) -> FetchContext:
    """Open quota + data/raw/{run_id}/. One context per run; callers pass it explicitly."""
    load_env()
    api_key = require_env("YOUTUBE_API_KEY")
    run_id = new_run_id()
    ctx = FetchContext(
        client=build_youtube(api_key),
        quota=QuotaBudget(daily_limit=daily_limit),
        run_id=run_id,
        run_dir=raw_run_dir(run_id),
        playlist_ids=load_playlist_cache(),
    )
    logger.info(
        "fetch run_id=%s pacific=%s remaining=%s",
        run_id,
        ctx.quota.pacific_date,
        ctx.quota.remaining(),
    )
    return ctx
