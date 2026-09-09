"""Name, handle and channel-title text rules. No network calls here."""
from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

from etl.utils import dedupe

# Billboard spells a few acts differently from Wikidata / YouTube.
_ALIASES: dict[str, tuple[str, ...]] = {
    "Ye": ("Kanye West",),
    "Beyonce": ("Beyoncé",),
    "T.I.": ("T.I",),
}
# Each handle guess costs one quota unit, so try only a handful.
MAX_HANDLE_GUESSES = 6
# Below this length a label is too generic to prove a title mismatch.
MIN_MATCH_LENGTH = 4


def search_names(artist_name: str) -> list[str]:
    """Label variants for Wikidata lookups and handle guesses."""
    name = artist_name.strip()
    names = [name]
    if ":" in artist_name:
        names.append(artist_name.split(":", 1)[0].strip())
    names.extend(_ALIASES.get(name, ()))
    return dedupe(names)


def load_handle_overrides(path: Path) -> dict[str, str]:
    """artist_name -> YouTube @handle (no @), from the committed overrides CSV."""
    if not path.exists():
        return {}
    overrides: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("artist_name") or "").strip()
            handle = (row.get("handle") or "").strip().lstrip("@")
            if name and handle:
                overrides[name] = handle
    return overrides


def handle_candidates(artist_name: str, overrides: dict[str, str]) -> list[str]:
    """Plausible @handles for a Billboard name, best guess first."""
    candidates: list[str] = []
    if artist_name in overrides:
        candidates.append(overrides[artist_name])
    for label in search_names(artist_name):
        compact = re.sub(r"[^A-Za-z0-9]", "", label)
        if compact:
            candidates += [compact, f"{compact}Official", f"{compact}Music"]
    return dedupe(candidates, key=str.lower)[:MAX_HANDLE_GUESSES]


def is_topic_channel(title: str) -> bool:
    return title.endswith(" - Topic")


def is_vevo_channel(title: str) -> bool:
    return "VEVO" in title.upper()


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", text).lower())


def title_plausibly_matches(artist_name: str, title: str) -> bool:
    """Drop hits whose channel title is a different person or org."""
    folded = [_fold(label) for label in search_names(artist_name)]
    labels = [label for label in folded if len(label) >= MIN_MATCH_LENGTH]
    if not labels:
        return True
    compact_title = _fold(title)
    return any(label in compact_title for label in labels)
