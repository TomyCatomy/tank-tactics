"""Validation of how often a rule fires: at most once a minute, at least once in every
calendar month and never more than 35 days apart (NOTES.md 28)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from .rules import DAY, RecurrenceError, normalize_rule, numbered_byday, parse_rule, pure_period
from .sampling import SAMPLE_MONTHS, sample
from .zones import check_timezone, epoch

MIN_GAP_SECONDS = 60  # occurrences are on round minutes, so at most one a minute
# At least one tick in every calendar month: "the last Thursday" can be 35 days apart.
# The hour is for a DST change in between, which makes that wall-clock gap an hour longer.
MAX_GAP_SECONDS = 35 * DAY + 3600
_MAX_PERIOD = 28 * DAY  # plain intervals: 28 days is the longest step that never skips a month
_MONTH_LEVEL = {"BYMONTH", "BYMONTHDAY", "BYSETPOS"}  # parts that can leave whole months out


@dataclass(frozen=True)
class RuleStats:
    min_gap: float  # shortest time between two occurrences seen in the sample
    max_gap: float  # longest, including the wait from the start to the first one


def _cadence(parts: dict[str, str]) -> None:
    """Structural cadence rules, with messages that say what to use instead."""
    freq, n = parts["FREQ"], int(parts.get("INTERVAL", "1"))
    if freq == "SECONDLY":
        raise RecurrenceError("FREQ=SECONDLY is not allowed: ticks happen on whole minutes, at most once a "
                              "minute (FREQ=MINUTELY). For faster testing, use debug fast ticks")
    if freq == "YEARLY":
        raise RecurrenceError("FREQ=YEARLY fires less than once a month: the schedule has to fire every month")
    if freq == "MONTHLY" and n > 1:
        raise RecurrenceError("FREQ=MONTHLY with INTERVAL above 1 skips months: the schedule has to fire every month")
    if freq == "MONTHLY" and not {"BYMONTHDAY", "BYDAY"} & parts.keys():
        # Otherwise the day comes from the start date, and a game started on the 31st would skip months.
        raise RecurrenceError("FREQ=MONTHLY needs BYMONTHDAY or BYDAY to say which day, "
                              "e.g. BYMONTHDAY=1, BYMONTHDAY=-1 (the last day) or BYDAY=TH;BYSETPOS=-1")


def _skip_hint(parts: dict[str, str]) -> str:
    days = [int(d) for d in parts.get("BYMONTHDAY", "").split(",") if d]
    if any(d >= 29 for d in days):
        return " Use BYMONTHDAY=-1 for the last day of every month."
    if "5" in parts.get("BYSETPOS", "").split(",") or any(abs(x) == 5 for x in numbered_byday(parts)):
        return " Use -1 for the last of that weekday in every month (BYDAY=-1TH, or BYSETPOS=-1)."
    return ""


def _check_months(epochs: list[float], zone: ZoneInfo, ref: datetime, parts: dict[str, str]) -> None:
    """Every whole calendar month after ``ref``'s, within the sample, has an occurrence."""
    have = {(d.year, d.month) for d in (datetime.fromtimestamp(e, zone) for e in epochs)}
    y, m = ref.year, ref.month
    end = (ref.year * 12 + ref.month - 1) + SAMPLE_MONTHS  # the horizon's month, not whole
    while True:
        m += 1
        if m > 12:
            y, m = y + 1, 1
        if y * 12 + m - 1 >= end:
            return
        if (y, m) not in have:
            name = datetime(y, m, 1).strftime("%B %Y")
            raise RecurrenceError(f"skips months (no tick in {name} when started on {ref:%d %B %Y}): "
                                  f"the schedule has to fire every month.{_skip_hint(parts)}")


@lru_cache(maxsize=1024)
def rule_stats(rule: str, tz: str) -> RuleStats:
    """Sample the rule from several reference starts and check its cadence. Raises
    RecurrenceError if it never fires, skips a calendar month or leaves more than 35 days
    between ticks."""
    parts = parse_rule(rule)
    check_timezone(tz)
    _cadence(parts)
    period = pure_period(parts)
    if period is not None:
        if period > _MAX_PERIOD:
            raise RecurrenceError("fires less than once a month (plain intervals can be at most 28 days, 672 hours)")
        return RuleStats(float(period), float(period))
    zone = ZoneInfo(tz)
    between: list[float] = []  # gaps between consecutive occurrences
    waits: list[float] = []  # from the start to the first one
    month_checks = []  # run after the gap check, whose message is clearer for long gaps
    # Starts at midnight, mid-month mid-day, and the last minute of a 31st (for parts derived from DTSTART).
    for ref in (datetime(2024, 1, 1), datetime(2025, 7, 16, 13, 37), datetime(2024, 1, 31, 23, 59)):
        epochs, full = sample(rule, zone, ref)
        if not epochs:
            raise RecurrenceError("never fires (or fires less than once a month)")
        between += [b - a for a, b in zip(epochs, epochs[1:])]
        waits.append(epochs[0] - epoch(ref, zone))
        if full:
            month_checks.append((epochs, ref))
        elif _MONTH_LEVEL & parts.keys() or numbered_byday(parts):
            # So many ticks a day that the sample can't reach far enough to see every month.
            raise RecurrenceError("combines many ticks a day with day-of-month rules, which can't be checked: "
                                  "simplify it (fewer ticks a day, or no BYMONTH/BYMONTHDAY/BYSETPOS)")
    max_gap = max(between + waits)
    if max_gap > MAX_GAP_SECONDS:
        raise RecurrenceError(f"leaves {max_gap / DAY:.1f} days between ticks: at most 35 days are allowed, "
                              f"so it fires every month.{_skip_hint(parts)}")
    for epochs, ref in month_checks:
        _check_months(epochs, zone, ref, parts)
    min_gap = min(between) if between else max_gap
    return RuleStats(min_gap, max_gap)


def validate(rule: Any, tz: Any) -> str:
    """Check a rule and zone, including its cadence; returns the normalized rule."""
    norm = normalize_rule(rule)
    check_timezone(tz)
    stats = rule_stats(norm, tz)
    if stats.min_gap < MIN_GAP_SECONDS:
        raise RecurrenceError(f"fires more than once a minute (gaps of {stats.min_gap:g} s)")
    return norm
