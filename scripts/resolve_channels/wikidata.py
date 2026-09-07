"""Wikidata P2397 (YouTube channel ID) lookups over SPARQL."""
from __future__ import annotations

import logging
import time

import requests

from .names import dedupe, search_names

logger = logging.getLogger("resolve_channels")

SPARQL_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "big-project-1-jde-pipeline/0.1 (educational; YouTube cohort freeze)"
MAX_ATTEMPTS = 6
# Wikidata items that are works, not the performing artist.
_SKIP_P31 = {
    "Q134556",  # single
    "Q7366",  # song
    "Q482994",  # album
    "Q11424",  # film
    "Q5398426",  # television series
    "Q3305213",  # painting
}


def _sparql(query: str) -> dict:
    """Run one SPARQL query, backing off exponentially on HTTP 429."""
    delay = 2.0
    for _ in range(MAX_ATTEMPTS):
        resp = requests.get(
            SPARQL_URL,
            params={"query": query, "format": "json"},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/sparql-results+json",
            },
            timeout=90,
        )
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp.json()
        logger.warning("rate-limited; sleeping %.1fs", delay)
        time.sleep(delay)
        delay = min(delay * 2, 60)
    raise requests.HTTPError(f"429 Too Many Requests after {MAX_ATTEMPTS} attempts ({SPARQL_URL})")


def _escape(label: str) -> str:
    return label.replace("\\", "\\\\").replace('"', '\\"')


def channels_for_labels(labels: list[str]) -> dict[str, str]:
    """One SPARQL round-trip: casefolded English label -> UC channel ID."""
    unique = dedupe(labels, key=str.casefold)
    if not unique:
        return {}

    values = " ".join(f'"{_escape(label)}"@en' for label in unique)
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

    mapping: dict[str, str] = {}
    for binding in _sparql(query).get("results", {}).get("bindings", []):
        label = binding.get("label", {}).get("value", "")
        channel = binding.get("channel", {}).get("value", "")
        if label and channel.startswith("UC") and label.casefold() not in mapping:
            mapping[label.casefold()] = channel
    logger.info("wikidata SPARQL matched %s/%s labels", len(mapping), len(unique))
    return mapping


def lookup_wikidata(artist_name: str, label_map: dict[str, str]) -> str | None:
    """First label variant of this artist that has a P2397 channel ID."""
    for label in search_names(artist_name):
        channel_id = label_map.get(label.casefold())
        if channel_id:
            return channel_id
    return None
