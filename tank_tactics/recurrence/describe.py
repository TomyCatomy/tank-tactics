"""One-line, human-readable summaries of rules, e.g. 'Every weekday at 09:00 (Asia/Jerusalem)'."""
from __future__ import annotations

from .rules import BYDAY_RE, DAY, WEEKDAYS, RecurrenceError, parse_rule, pure_period

_DAY_NAMES = dict(zip(WEEKDAYS, ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))
_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last", -2: "second-to-last"}
_UNITS = {"HOURLY": "hour", "DAILY": "day", "WEEKLY": "week", "MONTHLY": "month"}


def describe(rule: str, tz: str) -> str:
    """A one-line, human-readable summary, e.g. 'Every weekday at 09:00 (Asia/Jerusalem)'."""
    try:
        parts = parse_rule(rule)
    except RecurrenceError:
        return "Invalid rule"
    return f"{_describe(parts)} ({tz})"


def _describe(p: dict[str, str]) -> str:
    freq, n = p["FREQ"], int(p.get("INTERVAL", "1"))
    period = pure_period(p)
    if period is not None:
        return f"Every {_duration(period)}"
    extra = set(p) - {"FREQ", "INTERVAL", "BYHOUR", "BYMINUTE", "BYSECOND", "BYDAY", "BYMONTHDAY", "BYSETPOS"}
    if extra or freq not in _UNITS:
        return f"Custom rule: {';'.join(f'{k}={v}' for k, v in p.items())}"
    at = _time_of_day(p)
    every = (f"Every {_UNITS[freq]}" if n == 1 else f"Every {n} {_UNITS[freq]}s")
    if freq == "HOURLY":
        if set(p) - {"FREQ", "INTERVAL", "BYMINUTE", "BYSECOND"} or "," in p.get("BYMINUTE", ""):
            return f"Custom rule: {';'.join(f'{k}={v}' for k, v in p.items())}"
        return f"{every} at minute {int(p.get('BYMINUTE', '0')):02d}"
    if at is None:
        return f"Custom rule: {';'.join(f'{k}={v}' for k, v in p.items())}"
    days = p.get("BYDAY")
    if freq == "DAILY" and "BYMONTHDAY" not in p and "BYSETPOS" not in p:
        if days is None:
            return every + (" at the game's start time (to the minute)" if at == "" else at)
        if days == "MO,TU,WE,TH,FR" and n == 1:
            return "Every weekday (Mon–Fri)" + (at or " at the game's start time (to the minute)")
    if freq == "WEEKLY" and "BYMONTHDAY" not in p and "BYSETPOS" not in p:
        if days is None:
            return ("Weekly" if n == 1 else every) + " on the weekday the game started" + at
        names = days.split(",")
        if all(d in WEEKDAYS for d in names):
            if sorted(names, key=WEEKDAYS.index) == list(WEEKDAYS[:5]) and n == 1:
                return "Every weekday (Mon–Fri)" + at
            label = ", ".join(_DAY_NAMES[d] for d in sorted(names, key=WEEKDAYS.index))
            return ("Weekly" if n == 1 else every) + f" on {label}" + at
    if freq == "MONTHLY":
        lead = "Monthly" if n == 1 else every
        if set(p) & {"BYMONTHDAY"} and not (set(p) & {"BYDAY", "BYSETPOS"}):
            md = p["BYMONTHDAY"].split(",")
            if len(md) == 1:
                d = int(md[0])
                return lead + (" on the last day" if d == -1 else f" on day {d}") + at
        nth = _nth_weekday(p)
        if nth is not None:
            k, day = nth
            if k in _ORDINALS:
                return f"{lead} on the {_ORDINALS[k]} {_DAY_NAMES[day]}" + at
    return f"Custom rule: {';'.join(f'{k}={v}' for k, v in p.items())}"


def _nth_weekday(p: dict[str, str]) -> tuple[int, str] | None:
    """(n, weekday) for 'BYDAY=TH;BYSETPOS=4' or 'BYDAY=4TH', else None."""
    if "BYMONTHDAY" in p or "BYDAY" not in p or "," in p["BYDAY"]:
        return None
    m = BYDAY_RE.match(p["BYDAY"])
    if not m:
        return None
    if m.group(1) is not None and "BYSETPOS" not in p:
        return int(m.group(1)), m.group(2)
    if m.group(1) is None and "BYSETPOS" in p and "," not in p["BYSETPOS"]:
        return int(p["BYSETPOS"]), m.group(2)
    return None


def _time_of_day(p: dict[str, str]) -> str | None:
    """' at 09:00' (or ' at 09:00, 18:30'), '' when the rule uses the start time, None if too complex."""
    if "BYHOUR" not in p and "BYMINUTE" not in p and "BYSECOND" not in p:
        return ""
    if "BYHOUR" not in p:
        return None
    hours = [int(h) for h in p["BYHOUR"].split(",")]
    minutes = [int(m) for m in p.get("BYMINUTE", "0").split(",")]
    seconds = [int(s) for s in p.get("BYSECOND", "0").split(",")]
    if len(minutes) != 1 or seconds != [0]:
        return None
    return " at " + ", ".join(f"{h:02d}:{minutes[0]:02d}" for h in sorted(hours))


def _duration(seconds: int) -> str:
    if seconds % DAY == 0:
        n = seconds // DAY
        return "24 hours" if n == 1 else f"{n} days"
    for unit, size in (("hour", 3600), ("minute", 60)):
        if seconds % size == 0:
            n = seconds // size
            return unit if n == 1 else f"{n} {unit}s"
    return "second" if seconds == 1 else f"{seconds} seconds"
