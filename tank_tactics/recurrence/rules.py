"""Parsing an RRULE body (with dateutil) and the game's restrictions on what it may contain."""
from __future__ import annotations

import calendar
import re
from datetime import datetime
from typing import Any

from dateutil.rrule import rrule, rrulestr

DAY = 86_400
DEFAULT_RULE = "FREQ=DAILY"  # every 24 hours from the start: the original fixed interval
DEFAULT_TIMEZONE = "UTC"
MAX_RULE_LENGTH = 300
_UNIT_SECONDS = {"SECONDLY": 1, "MINUTELY": 60, "HOURLY": 3600}
_MAX_INTERVAL = 366 * DAY
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
BYDAY_RE = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")
_PARTS = {"FREQ", "INTERVAL", "WKST", "BYDAY", "BYSECOND", "BYMINUTE", "BYHOUR", "BYMONTHDAY", "BYMONTH", "BYSETPOS"}
_PARSE_DTSTART = datetime(2000, 1, 1)  # dateutil needs one to build the rule; only its parts are kept
BY_PARTS = {k for k in _PARTS if k.startswith("BY")}


class RecurrenceError(ValueError):
    """An invalid rule or time zone. The message is safe to show to players."""

def parse_rule(text: Any) -> dict[str, str]:
    """Parse an RRULE body with dateutil and reject anything this game can't use.

    Returns dateutil's canonical spelling of the parts (upper-case, in RFC order, e.g.
    ``{"FREQ": "WEEKLY", "BYDAY": "MO,TH", "BYHOUR": "9"}``)."""
    if not isinstance(text, str):
        raise RecurrenceError("must be a string")
    s = text.strip()
    if "DTSTART" in s.upper():
        raise RecurrenceError("DTSTART is not allowed: the schedule starts when the game starts")
    if "\n" in s or "\r" in s:
        raise RecurrenceError("must be a single RRULE line")
    if len(s) > MAX_RULE_LENGTH:
        raise RecurrenceError(f"is too long (max {MAX_RULE_LENGTH} characters)")
    s = s.removeprefix("RRULE:").removeprefix("rrule:").rstrip(";")
    if not s:
        raise RecurrenceError("is empty: give a rule such as FREQ=DAILY;BYHOUR=9;BYMINUTE=0")
    # Which parts were written, so the game's restrictions get clear messages (and so
    # nothing dateutil would quietly drop or override gets through). dateutil parses the values.
    given: dict[str, str] = {}
    for chunk in s.split(";"):
        key, sep, value = (x.strip().upper() for x in chunk.partition("="))
        if not sep or not key or not value:
            raise RecurrenceError(f"'{chunk.strip()}' is not KEY=VALUE")
        if key in ("COUNT", "UNTIL"):
            raise RecurrenceError(f"{key} is not allowed: the schedule runs until the game ends")
        if key in ("BYWEEKNO", "BYYEARDAY"):
            raise RecurrenceError(f"{key} is not supported: the schedule has to fire every month")
        if key not in _PARTS:
            raise RecurrenceError(f"unsupported part '{key}'")
        if key in given:
            raise RecurrenceError(f"{key} is given twice")
        given[key] = value
    if "FREQ" not in given:
        raise RecurrenceError("FREQ is required, e.g. FREQ=DAILY")
    try:
        parsed = rrulestr(s, dtstart=_PARSE_DTSTART)
    except ValueError as e:
        raise RecurrenceError(str(e)) from None
    body = str(parsed).splitlines()[-1].removeprefix("RRULE:")
    parts = dict(chunk.split("=", 1) for chunk in body.split(";"))
    # dateutil checks hours, minutes, seconds, BYSETPOS and names, but not these (it
    # drops BYMONTHDAY=0, and the number in BYDAY=2MO unless FREQ is MONTHLY or YEARLY).
    if not 1 <= int(given.get("INTERVAL", "1")) <= _MAX_INTERVAL:
        raise RecurrenceError(f"INTERVAL must be between 1 and {_MAX_INTERVAL}")
    for key, lo, hi in (("BYMONTH", 1, 12), ("BYMONTHDAY", -31, 31)):
        if key in given and not all(lo <= int(v) <= hi and int(v) != 0 for v in given[key].split(",")):
            raise RecurrenceError(f"{key} values must be between {lo} and {hi}, not 0")
    freq = parts["FREQ"]
    if freq not in ("MONTHLY", "YEARLY") and re.search(r"\d", given.get("BYDAY", "")):
        raise RecurrenceError("numbered BYDAY values (like 2TU) need FREQ=MONTHLY")
    if parts.get("BYSECOND", "0") != "0":
        raise RecurrenceError("BYSECOND can only be 0: ticks happen on whole minutes")
    if freq == "SECONDLY" and BY_PARTS & parts.keys():
        raise RecurrenceError("FREQ=SECONDLY can only be a plain interval (FREQ=SECONDLY;INTERVAL=N)")
    if "BYSETPOS" in parts and not (BY_PARTS - {"BYSETPOS"}) & parts.keys():
        raise RecurrenceError("BYSETPOS needs another BY... part to pick from")
    _static_satisfiable(parts)
    return parts


def normalize_rule(text: Any) -> str:
    """The rule as stored: dateutil's canonical spelling, without an ``RRULE:`` prefix."""
    return ";".join(f"{k}={v}" for k, v in parse_rule(text).items())

def numbered_byday(parts: dict[str, str]) -> list[int]:
    """The ordinals in BYDAY (e.g. [4, -1] for 4TH,-1FR)."""
    out = []
    for d in parts.get("BYDAY", "").split(","):
        m = BYDAY_RE.match(d)
        if m and m.group(1):
            out.append(int(m.group(1)))
    return out

def _static_satisfiable(parts: dict[str, str]) -> None:
    """Catch the common rules that can never fire without asking dateutil, which would
    search up to the year 9999 for them."""
    months = [int(m) for m in parts["BYMONTH"].split(",")] if "BYMONTH" in parts else list(range(1, 13))
    if "BYMONTHDAY" in parts:
        days = [int(d) for d in parts["BYMONTHDAY"].split(",")]
        if not any(abs(d) <= calendar.monthrange(2024, m)[1] for d in days for m in months):  # a leap year
            raise RecurrenceError("never fires: no month in BYMONTH has that BYMONTHDAY")

def on_round_minutes(rule: str) -> bool:
    """Whether this rule's ticks (and the game's deadline) are on whole minutes.
    Only debug fast ticks (FREQ=SECONDLY) aren't."""
    return parse_rule(rule)["FREQ"] != "SECONDLY"

def debug_rule(tick_seconds: int) -> str:
    return f"FREQ=SECONDLY;INTERVAL={int(tick_seconds)}"

def pure_period(parts: dict[str, str]) -> int | None:
    """Seconds between occurrences for a plain sub-daily interval, else None."""
    unit = _UNIT_SECONDS.get(parts["FREQ"])
    if unit is None or BY_PARTS & parts.keys():
        return None
    return unit * int(parts.get("INTERVAL", "1"))


def _effective_text(parts: dict[str, str]) -> str:
    """Make implied time parts explicit (see the package docstring)."""
    p = dict(parts)
    # Only parts finer than FREQ: with FREQ=MINUTELY the minute is what repeats.
    if "BYHOUR" in p and "BYMINUTE" not in p and p["FREQ"] != "MINUTELY":
        p["BYMINUTE"] = "0"
    if p["FREQ"] != "SECONDLY":
        p.setdefault("BYSECOND", "0")  # round minutes
    return ";".join(f"{k}={v}" for k, v in p.items())


def compile_rule(rule: str, dtstart_wall: datetime) -> rrule:
    r = rrulestr(_effective_text(parse_rule(rule)), dtstart=dtstart_wall)
    assert isinstance(r, rrule)
    return r
