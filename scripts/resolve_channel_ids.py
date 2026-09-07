"""One-off: fill channel_id on artists_registry.csv.

Resolution order (never search.list, never youtube.com/results scrape):
  1. Existing CSV value (re-runs)
  2. scripts/channel_overrides.csv @handle → channels.list(forHandle=)
  3. Wikidata property P2397 (YouTube channel ID)
  4. Guessed handles → channels.list(forHandle=)

Then batch-validate with channels.list(id=) and store channel_title.
"""
from __future__ import annotations

import csv
import logging
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from etl.utils import require_env

REGISTRY = ROOT / "data" / "processed" / "artists_registry.csv"
OVERRIDES = ROOT / "scripts" / "channel_overrides.csv"
WIKIDATA_UA = "big-project-1-jde-pipeline/0.1 (educational; YouTube cohort freeze)"
# Skip Wikidata hits that are works, not the performing artist.
_SKIP_P31 = {
    "Q134556",  # single
    "Q7366",  # song
    "Q482994",  # album
    "Q11424",  # film
    "Q5398426",  # television series
    "Q3305213",  # painting
}

logger = logging.getLogger("resolve_channels")


def _redact(text: str) -> str:
    return re.sub(r"key=[^&\s]+", "key=REDACTED", text)


def _request_json(url: str, *, params: dict, headers: dict, timeout: int = 60) -> dict:
    """GET JSON with backoff on HTTP 429."""
    delay = 2.0
    last_error: Exception | None = None
    for attempt in range(6):
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 429:
            last_error = requests.HTTPError(f"429 Too Many Requests ({url})")
            logger.warning("rate-limited; sleeping %.1fs", delay)
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        resp.raise_for_status()
        return resp.json()
    raise last_error or RuntimeError(f"request failed: {url}")


def _sparql_escape(label: str) -> str:
    return label.replace("\\", "\\\\").replace('"', '\\"')


def search_names(artist_name: str) -> list[str]:
    """Label variants for Wikidata / handle guesses."""
    names = [artist_name.strip()]
    if ":" in artist_name:
        names.append(artist_name.split(":", 1)[0].strip())
    if artist_name.strip() == "Ye":
        names.append("Kanye West")
    if artist_name.strip() == "Beyonce":
        names.append("Beyoncé")
    if artist_name.strip() == "T.I.":
        names.append("T.I.")
        names.append("T.I")
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def wikidata_channels_for_labels(labels: list[str]) -> dict[str, str]:
    """One SPARQL round-trip: casefolded English label -> UC channel ID."""
    unique: list[str] = []
    seen: set[str] = set()
    for label in labels:
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            unique.append(label)
    if not unique:
        return {}

    values = " ".join(f'"{_sparql_escape(label)}"@en' for label in unique)
    skip = " ".join(
        f"FILTER NOT EXISTS {{ ?item wdt:P31 wd:{qid} }}" for qid in sorted(_SKIP_P31)
    )
    query = f"""
    SELECT ?label ?channel WHERE {{
      VALUES ?label {{ {values} }}
      {{ ?item rdfs:label ?label. }}
      UNION
      {{ ?item skos:altLabel ?label. }}
      ?item wdt:P2397 ?channel.
      FILTER(STRSTARTS(STR(?channel), "UC"))
      {skip}
    }}
    """
    data = _request_json(
        "https://query.wikidata.org/sparql",
        params={"query": query, "format": "json"},
        headers={
            "User-Agent": WIKIDATA_UA,
            "Accept": "application/sparql-results+json",
        },
        timeout=90,
    )
    mapping: dict[str, str] = {}
    for binding in data.get("results", {}).get("bindings", []):
        label = binding.get("label", {}).get("value", "")
        channel = binding.get("channel", {}).get("value", "")
        if label and channel.startswith("UC") and label.casefold() not in mapping:
            mapping[label.casefold()] = channel
    logger.info("wikidata SPARQL matched %s/%s labels", len(mapping), len(unique))
    return mapping


def lookup_wikidata(artist_name: str, wiki_map: dict[str, str]) -> str | None:
    for label in search_names(artist_name):
        hit = wiki_map.get(label.casefold())
        if hit:
            return hit
    return None


def load_handle_overrides() -> dict[str, str]:
    """artist_name -> YouTube @handle (no @). Resolved on VM via channels.list(forHandle=)."""
    if not OVERRIDES.exists():
        return {}
    mapping: dict[str, str] = {}
    with OVERRIDES.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("artist_name") or "").strip()
            handle = (row.get("handle") or "").strip().lstrip("@")
            if name and handle:
                mapping[name] = handle
    return mapping


def handle_candidates(artist_name: str, handle_overrides: dict[str, str]) -> list[str]:
    """Plausible @handles from a Billboard name. Few tries — each costs 1 quota unit."""
    out: list[str] = []
    if artist_name in handle_overrides:
        out.append(handle_overrides[artist_name])
    for label in search_names(artist_name):
        compact = re.sub(r"[^A-Za-z0-9]", "", label)
        if compact:
            out.append(compact)
            out.append(f"{compact}Official")
            out.append(f"{compact}Music")
        spaced = re.sub(r"[^A-Za-z0-9]+", "", label.replace(" ", ""))
        if spaced:
            out.append(spaced)
    seen: list[str] = []
    for handle in out:
        if handle.lower() not in {h.lower() for h in seen}:
            seen.append(handle)
        if len(seen) >= 6:
            break
    return seen


def youtube_client(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def lookup_handle(youtube, handle: str) -> str | None:
    """YouTube Data API channels.list(forHandle=). Works on a VM with YOUTUBE_API_KEY."""
    handle = handle.lstrip("@")
    try:
        resp = youtube.channels().list(part="id,snippet", forHandle=handle).execute()
    except HttpError as err:
        logger.debug("forHandle %s: %s", handle, _redact(str(err)))
        return None
    items = resp.get("items") or []
    if not items:
        return None
    title = items[0].get("snippet", {}).get("title", "")
    if title.endswith(" - Topic"):
        return None
    if "VEVO" in title.upper() and "VEVO" not in handle.upper():
        return None
    return items[0]["id"]


def resolve_for_handle(youtube, artist_name: str, handle_overrides: dict[str, str]) -> str | None:
    for handle in handle_candidates(artist_name, handle_overrides):
        channel_id = lookup_handle(youtube, handle)
        if channel_id:
            return channel_id
    return None


def validate_channels(youtube, channel_ids: list[str]) -> dict[str, str]:
    """channel_id -> channel_title for IDs that still exist. 50 IDs per call."""
    titles: dict[str, str] = {}
    unique = list(dict.fromkeys(channel_ids))
    for i in range(0, len(unique), 50):
        batch = unique[i : i + 50]
        try:
            resp = youtube.channels().list(
                part="snippet",
                id=",".join(batch),
            ).execute()
        except HttpError as err:
            raise RuntimeError(_redact(str(err))) from err
        for item in resp.get("items") or []:
            titles[item["id"]] = item.get("snippet", {}).get("title", "")
    return titles


def is_topic_channel(title: str) -> bool:
    return title.endswith(" - Topic")


def is_vevo_channel(title: str) -> bool:
    return "VEVO" in title.upper()


def title_plausibly_matches(artist_name: str, title: str) -> bool:
    """Drop Wikidata hits whose channel title is a different person or org."""

    def fold(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text)
        return re.sub(r"[^a-z0-9]", "", normalized.lower())

    compact_title = fold(title)
    long_labels = [
        fold(label) for label in search_names(artist_name) if len(fold(label)) >= 4
    ]
    if not long_labels:
        return True
    return any(label in compact_title for label in long_labels)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )
    load_dotenv(ROOT / ".env")
    api_key = require_env("YOUTUBE_API_KEY")

    if not REGISTRY.exists():
        raise SystemExit(f"Missing {REGISTRY}. Run: uv run python scripts/fetch_artist_100.py")

    with REGISTRY.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    handle_overrides = load_handle_overrides()
    youtube = youtube_client(api_key)

    wiki_labels: list[str] = []
    for row in rows:
        wiki_labels.extend(search_names(row["artist_name"]))
    wiki_map = wikidata_channels_for_labels(wiki_labels)

    def resolve_one(row: dict, *, skip_wikidata: bool = False) -> None:
        name = row["artist_name"]

        # Committed @handles always win (YouTube API). Re-runs stay reproducible on a VM.
        if name in handle_overrides:
            handle_id = lookup_handle(youtube, handle_overrides[name])
            if handle_id:
                row["channel_id"] = handle_id
                row["id_resolution_method"] = "channels.list.forHandle"
                logger.info("forHandle %s (@%s) -> %s", name, handle_overrides[name], handle_id)
                return

        existing = (row.get("channel_id") or "").strip()
        if existing.startswith("UC"):
            return

        if not skip_wikidata:
            wiki_id = lookup_wikidata(name, wiki_map)
            if wiki_id:
                row["channel_id"] = wiki_id
                row["id_resolution_method"] = "wikidata_p2397"
                logger.info("wikidata  %s -> %s", name, wiki_id)
                return

        handle_id = resolve_for_handle(youtube, name, handle_overrides)
        if handle_id:
            row["channel_id"] = handle_id
            row["id_resolution_method"] = "channels.list.forHandle"
            logger.info("forHandle %s -> %s", name, handle_id)
            return

        logger.warning("UNRESOLVED %s", name)

    for row in rows:
        resolve_one(row)

    def apply_titles(titles: dict[str, str], *, reject_vevo: bool) -> list[dict]:
        rejected: list[dict] = []
        for row in rows:
            cid = (row.get("channel_id") or "").strip()
            title = titles.get(cid, "")
            if cid and is_topic_channel(title):
                logger.warning("Topic channel rejected for %s (%s)", row["artist_name"], cid)
                row["channel_id"] = ""
                row["channel_title"] = ""
                row["id_resolution_method"] = ""
                rejected.append(row)
                continue
            if cid and title and not title_plausibly_matches(row["artist_name"], title):
                if row["artist_name"] not in handle_overrides:
                    logger.warning(
                        "Title mismatch rejected for %s (%s / %s)",
                        row["artist_name"],
                        cid,
                        title,
                    )
                    row["channel_id"] = ""
                    row["channel_title"] = ""
                    row["id_resolution_method"] = ""
                    rejected.append(row)
                    continue
            if (
                cid
                and reject_vevo
                and is_vevo_channel(title)
                and "VEVO" not in handle_overrides.get(row["artist_name"], "").upper()
            ):
                logger.warning("VEVO channel rejected for %s (%s / %s)", row["artist_name"], cid, title)
                row["channel_id"] = ""
                row["channel_title"] = ""
                row["id_resolution_method"] = ""
                rejected.append(row)
                continue
            if cid and cid not in titles:
                logger.warning("Invalid/deleted channel for %s (%s)", row["artist_name"], cid)
                row["channel_id"] = ""
                row["channel_title"] = ""
                row["id_resolution_method"] = ""
                rejected.append(row)
                continue
            row["channel_title"] = title
            if is_vevo_channel(title):
                logger.warning("VEVO exception kept for %s (%s / %s)", row["artist_name"], cid, title)
        return rejected

    ids = [r["channel_id"] for r in rows if (r.get("channel_id") or "").startswith("UC")]
    titles = validate_channels(youtube, ids)
    rejected = apply_titles(titles, reject_vevo=True)
    if rejected:
        logger.info("Retrying %s rejected IDs via forHandle", len(rejected))
        for row in rejected:
            resolve_one(row, skip_wikidata=True)
        ids = [r["channel_id"] for r in rows if (r.get("channel_id") or "").startswith("UC")]
        titles = validate_channels(youtube, ids)
        still = apply_titles(titles, reject_vevo=True)
        for row in still:
            wiki_id = lookup_wikidata(row["artist_name"], wiki_map)
            if wiki_id:
                row["channel_id"] = wiki_id
                row["id_resolution_method"] = "wikidata_p2397"
                logger.warning("last-resort Wikidata %s -> %s", row["artist_name"], wiki_id)
        ids = [r["channel_id"] for r in rows if (r.get("channel_id") or "").startswith("UC")]
        titles = validate_channels(youtube, ids)
        apply_titles(titles, reject_vevo=False)

    valid = [r["channel_id"] for r in rows if (r.get("channel_id") or "").startswith("UC")]
    unique = set(valid)
    missing = [r["artist_name"] for r in rows if not (r.get("channel_id") or "").startswith("UC")]
    logger.info("resolved %s/100 unique=%s", len(valid), len(unique))
    if missing:
        logger.error("Missing channel_id (%s): %s", len(missing), "; ".join(missing))
        raise SystemExit(1)
    if len(unique) < 100:
        logger.error("Need 100 unique UC ids, got %s", len(unique))
        raise SystemExit(1)

    with REGISTRY.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"OK: 100 unique channel IDs written to {REGISTRY}")


if __name__ == "__main__":
    main()
