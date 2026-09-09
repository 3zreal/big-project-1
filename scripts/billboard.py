"""Minimal Billboard Artist 100 client.

Trimmed from billboard.py (MIT, Allen Guo): weekly Artist 100, new-style page only.
No year-end charts, old layout, images, or peak/weeks metadata.

Not imported by main.py / etl fetch. One-off freeze:

    python scripts/fetch_artist_100.py
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

CHART_URL = "https://www.billboard.com/charts/artist-100"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class BillboardNotFoundException(Exception):
    pass


class BillboardParseException(Exception):
    pass


@dataclass(frozen=True)
class ArtistEntry:
    rank: int
    artist_name: str


def fetch_artist_100(
    date: str | None = None,
    *,
    timeout: int = 25,
    max_retries: int = 5,
) -> tuple[str, list[ArtistEntry]]:
    """Return (chart_week YYYY-MM-DD, entries ranked 1..n).

    date: optional chart week (YYYY-MM-DD). None = latest published week.
    """
    if date is not None:
        if not re.match(r"\d{4}-\d{2}-\d{2}$", date):
            raise ValueError("date must be YYYY-MM-DD")
        url = f"{CHART_URL}/{date}"
    else:
        url = CHART_URL

    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})
    session.mount(
        "https://www.billboard.com",
        requests.adapters.HTTPAdapter(max_retries=max_retries),
    )
    resp = session.get(url, timeout=timeout)
    if resp.status_code == 404:
        raise BillboardNotFoundException(f"Chart not found: {url}")
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    return _parse_artist_100_page(soup)


def _parse_artist_100_page(soup: BeautifulSoup) -> tuple[str, list[ArtistEntry]]:
    picker = soup.select_one("#chart-date-picker")
    chart_week = (picker.get("data-date") if picker else None) or dt.date.today().isoformat()

    entries: list[ArtistEntry] = []
    for row in soup.select("ul.o-chart-results-list-row"):
        cells = row.select("li")
        rank_el = cells[0].select_one("span.c-label")
        title_el = cells[3].select_one("#title-of-a-story")
        artist_el = cells[3].select_one("#title-of-a-story + span.c-label")
        if not rank_el or not title_el:
            raise BillboardParseException("Failed to parse rank or title")

        try:
            rank = int(rank_el.text.strip())
        except ValueError as exc:
            raise BillboardParseException("Failed to parse rank") from exc

        # Artist 100 puts the name in the title slot and leaves the artist label empty.
        artist = (artist_el.text.strip() if artist_el else "") or title_el.text.strip()
        entries.append(ArtistEntry(rank=rank, artist_name=artist))

    if not entries:
        raise BillboardParseException("No chart rows found; Billboard HTML may have changed")

    entries.sort(key=lambda e: e.rank)
    return chart_week, entries
