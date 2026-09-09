"""Resolve, validate and write channel IDs onto the artist registry CSV."""
from __future__ import annotations

import logging

from etl.artist_registry import REGISTRY_PATH, read_registry, write_registry
from etl.utils import require_env
from etl.utils import bootstrap
from etl.utils import ROOT_DIR

from .names import (
    is_topic_channel,
    is_vevo_channel,
    load_handle_overrides,
    search_names,
    title_plausibly_matches,
)
from .wikidata import channels_for_labels, lookup_wikidata
from .youtube import build_client, lookup_handle, resolve_via_handles, validate_channels

REGISTRY = REGISTRY_PATH
OVERRIDES = ROOT_DIR / "scripts" / "channel_overrides.csv"
COHORT_SIZE = 100

logger = logging.getLogger("resolve_channels")


def _has_channel(row: dict) -> bool:
    return (row.get("channel_id") or "").startswith("UC")


def _set_channel(row: dict, channel_id: str, method: str) -> None:
    row["channel_id"] = channel_id
    row["id_resolution_method"] = method


def _clear_channel(row: dict) -> None:
    row["channel_id"] = ""
    row["channel_title"] = ""
    row["id_resolution_method"] = ""


def resolve_row(
    client,
    row: dict,
    overrides: dict[str, str],
    label_map: dict[str, str],
    *,
    skip_wikidata: bool = False,
) -> None:
    """Fill one row's channel_id: committed @handle, existing value, Wikidata, then guesses."""
    name = row["artist_name"]

    # Committed @handles always win, so re-runs stay reproducible on a VM.
    if name in overrides:
        channel_id = lookup_handle(client, overrides[name])
        if channel_id:
            _set_channel(row, channel_id, "channels.list.forHandle")
            logger.info("forHandle %s (@%s) -> %s", name, overrides[name], channel_id)
            return

    if (row.get("channel_id") or "").strip().startswith("UC"):
        return

    if not skip_wikidata:
        channel_id = lookup_wikidata(name, label_map)
        if channel_id:
            _set_channel(row, channel_id, "wikidata_p2397")
            logger.info("wikidata  %s -> %s", name, channel_id)
            return

    channel_id = resolve_via_handles(client, name, overrides)
    if channel_id:
        _set_channel(row, channel_id, "channels.list.forHandle")
        logger.info("forHandle %s -> %s", name, channel_id)
        return

    logger.warning("UNRESOLVED %s", name)


def _rejection_reason(
    name: str,
    channel_id: str,
    title: str,
    *,
    exists: bool,
    overrides: dict[str, str],
    reject_vevo: bool,
) -> str | None:
    """Why this channel must be dropped, or None to keep it."""
    if not channel_id:
        return None
    if is_topic_channel(title):
        return f"Topic channel ({channel_id})"
    if title and name not in overrides and not title_plausibly_matches(name, title):
        return f"Title mismatch ({channel_id} / {title})"
    if reject_vevo and is_vevo_channel(title) and "VEVO" not in overrides.get(name, "").upper():
        return f"VEVO channel ({channel_id} / {title})"
    if not exists:
        return f"Invalid/deleted channel ({channel_id})"
    return None


def _validate_pass(
    client,
    rows: list[dict],
    overrides: dict[str, str],
    *,
    reject_vevo: bool,
    titles: dict[str, str] | None = None,
) -> list[dict]:
    """Re-check every resolved ID, store titles, clear rejects and return the cleared rows.

    titles accumulates across passes, so a retry only validates IDs not yet seen.
    """
    titles = {} if titles is None else titles
    unseen = [r["channel_id"] for r in rows if _has_channel(r) and r["channel_id"] not in titles]
    if unseen:
        titles.update(validate_channels(client, unseen))

    rejected: list[dict] = []
    for row in rows:
        name = row["artist_name"]
        channel_id = (row.get("channel_id") or "").strip()
        title = titles.get(channel_id, "")
        reason = _rejection_reason(
            name, channel_id, title,
            exists=channel_id in titles, overrides=overrides, reject_vevo=reject_vevo,
        )
        if reason:
            logger.warning("%s rejected for %s", reason, name)
            _clear_channel(row)
            rejected.append(row)
            continue
        row["channel_title"] = title
        if is_vevo_channel(title):
            logger.warning("VEVO exception kept for %s (%s)", name, title)
    return rejected


def _require_full_cohort(rows: list[dict]) -> None:
    """Fail closed unless every artist ended up with a distinct UC channel ID."""
    resolved = [r["channel_id"] for r in rows if _has_channel(r)]
    missing = [r["artist_name"] for r in rows if not _has_channel(r)]
    unique = set(resolved)
    logger.info("resolved %s/%s unique=%s", len(resolved), COHORT_SIZE, len(unique))
    if missing:
        logger.error("Missing channel_id (%s): %s", len(missing), "; ".join(missing))
        raise SystemExit(1)
    if len(unique) < COHORT_SIZE:
        logger.error("Need %s unique UC ids, got %s", COHORT_SIZE, len(unique))
        raise SystemExit(1)


def main() -> None:
    bootstrap("resolve_channels.log", logger_name="resolve_channels")
    api_key = require_env("YOUTUBE_API_KEY")

    if not REGISTRY.exists():
        raise SystemExit(f"Missing {REGISTRY}. Run: uv run python scripts/fetch_artist_100.py")

    fieldnames, rows = read_registry()
    overrides = load_handle_overrides(OVERRIDES)
    client = build_client(api_key)
    label_map = channels_for_labels(
        [label for row in rows for label in search_names(row["artist_name"])]
    )

    for row in rows:
        resolve_row(client, row, overrides, label_map)

    titles: dict[str, str] = {}
    rejected = _validate_pass(client, rows, overrides, reject_vevo=True, titles=titles)
    if rejected:
        logger.info("Retrying %s rejected IDs via forHandle", len(rejected))
        for row in rejected:
            resolve_row(client, row, overrides, label_map, skip_wikidata=True)

        for row in _validate_pass(client, rows, overrides, reject_vevo=True, titles=titles):
            channel_id = lookup_wikidata(row["artist_name"], label_map)
            if channel_id:
                _set_channel(row, channel_id, "wikidata_p2397")
                logger.warning("last-resort Wikidata %s -> %s", row["artist_name"], channel_id)

        _validate_pass(client, rows, overrides, reject_vevo=False, titles=titles)

    _require_full_cohort(rows)
    write_registry(rows, fieldnames)
    print(f"OK: {COHORT_SIZE} unique channel IDs written to {REGISTRY}")
