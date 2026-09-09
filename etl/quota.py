"""One-run YouTube quota budget keyed by Pacific calendar date.

Read methods used here cost 1 unit each. Default daily cap is 10,000,
resetting at midnight America/Los_Angeles.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from etl.errors import QuotaStop
from etl.utils import load_json, write_json
from etl.utils import DATA_RAW_DIR

logger = logging.getLogger(__name__)

PACIFIC = ZoneInfo("America/Los_Angeles")
LIST_CALL_UNITS = 1
DEFAULT_DAILY_LIMIT = 10_000


def pacific_today() -> str:
    return datetime.now(PACIFIC).date().isoformat()


def _state_path(pacific_date: str) -> Path:
    return DATA_RAW_DIR / "quota" / f"{pacific_date}.json"


class QuotaBudget:
    """Persist units_spent for the current Pacific date; stop a run before overspend."""

    def __init__(self, *, daily_limit: int | None = None, pacific_date: str | None = None) -> None:
        self.pacific_date = pacific_date or pacific_today()
        self.daily_limit = daily_limit or int(
            os.getenv("YOUTUBE_QUOTA_DAILY", str(DEFAULT_DAILY_LIMIT))
        )
        self.path = _state_path(self.pacific_date)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.units_spent = self._load()

    def remaining(self) -> int:
        return max(0, self.daily_limit - self.units_spent)

    def ensure(self, units: int = LIST_CALL_UNITS) -> None:
        if self.remaining() < units:
            raise QuotaStop(
                f"Pacific {self.pacific_date}: remaining {self.remaining()} < {units}"
            )

    def record(self, units: int = LIST_CALL_UNITS) -> None:
        self.units_spent += units
        self._save()
        logger.debug(
            "quota pacific=%s spent=%s remaining=%s",
            self.pacific_date,
            self.units_spent,
            self.remaining(),
        )

    def snapshot(self) -> dict:
        return {
            "pacific_date": self.pacific_date,
            "units_spent": self.units_spent,
            "daily_limit": self.daily_limit,
            "remaining": self.remaining(),
        }

    def _load(self) -> int:
        payload = load_json(self.path, default={})
        if payload.get("pacific_date") != self.pacific_date:
            return 0
        return int(payload.get("units_spent") or 0)

    def _save(self) -> None:
        """Durable after every unit: a crashed run must not re-spend its quota."""
        write_json(self.path, self.snapshot())
