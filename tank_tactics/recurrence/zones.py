"""Time zones and conversions between epochs and a zone's wall clock."""
from __future__ import annotations

import math
from datetime import datetime
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from .rules import RecurrenceError


def check_timezone(name: Any) -> str:
    if not isinstance(name, str) or not name:
        raise RecurrenceError("must be an IANA time zone name such as Europe/London")
    if name not in _zones():
        raise RecurrenceError(f"unknown time zone '{name[:64]}' (use an IANA name such as Asia/Jerusalem)")
    return name


@lru_cache(maxsize=1)
def _zones() -> frozenset[str]:
    return frozenset(available_timezones() | {"UTC"})


def timezones() -> list[str]:
    """Every time zone the server accepts, sorted."""
    return sorted(_zones())

def wall(t: float, zone: ZoneInfo) -> datetime:
    """Naive local wall time of an epoch."""
    return datetime.fromtimestamp(t, zone).replace(tzinfo=None)


def epoch(w: datetime, zone: ZoneInfo) -> float:
    """Epoch of a naive local wall time (fold=0: gap times shift forward, repeated times use the first)."""
    return w.replace(tzinfo=zone).timestamp()

def round_up_to_minute(t: float) -> float:
    return math.ceil(t / 60) * 60.0
