"""RFC 5545 recurrence rules (RRULE) for the AP schedule.

Pure and self-contained: no clock, no I/O, no imports from the rest of the package,
so both ``config`` (validation) and ``game`` (the clock) can use it.

A schedule is an RRULE *body* (``FREQ=DAILY;BYHOUR=9;BYMINUTE=0``) plus an IANA time
zone. DTSTART is the game's start time. Occurrences are computed on the zone's wall
clock, so "every day at 09:00" stays at 09:00 local time across DST changes:

* a wall time that doesn't exist (skipped by a spring-forward change) happens that
  far later, as RFC 5545 says (02:30 in a 02:00->03:00 gap becomes 03:30);
* a wall time that happens twice (fall back) happens once, at the first of the two;
* two occurrences that land on the same instant count once.

Game rules on top of RFC 5545:

* **Round minutes.** Every occurrence is on a whole minute (second 0): DTSTART is the
  game start floored to the minute, BYSECOND is implicitly 0 (any other BYSECOND is
  refused), and only occurrences strictly after the real start count.
* **Cadence.** At most a minute apart (FREQ=SECONDLY is refused) and at least one tick
  in every calendar month, never more than 35 days apart (see ``validate``).
* Pure intervals below a day (``FREQ=HOURLY;INTERVAL=6`` with no BY* parts) count real
  elapsed time from the start, so DST never makes one longer or shorter.
* Time parts finer than the finest one given (and finer than FREQ) default to zero
  instead of to the start time's: ``BYHOUR=9`` means 09:00, not 09:<start minute>.

Parsing and expansion are dateutil's (``rrulestr``); this module adds the game's
policy on top. ``FREQ=SECONDLY;INTERVAL=N`` only exists for debug fast ticks, and keeps
their exact timing (DTSTART = the exact start, no rounding).
"""
from __future__ import annotations

import bisect
import calendar
import heapq
import math
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Iterator
from zoneinfo import ZoneInfo, available_timezones

from dateutil.rrule import rrule, rrulestr

DAY = 86_400
DEFAULT_RULE = "FREQ=DAILY"  # every 24 hours from the start: the original fixed interval
DEFAULT_TIMEZONE = "UTC"
MIN_GAP_SECONDS = 60  # occurrences are on round minutes, so at most one a minute
# At least one tick in every calendar month: "the last Thursday" can be 35 days apart.
# The hour is for a DST change in between, which makes that wall-clock gap an hour longer.
MAX_GAP_SECONDS = 35 * DAY + 3600
_MAX_PERIOD = 28 * DAY  # plain intervals: 28 days is the longest step that never skips a month
MAX_RULE_LENGTH = 300

_UNIT_SECONDS = {"SECONDLY": 1, "MINUTELY": 60, "HOURLY": 3600}
_MAX_INTERVAL = 366 * DAY
_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_BYDAY_RE = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")
_PARTS = {"FREQ", "INTERVAL", "WKST", "BYDAY", "BYSECOND", "BYMINUTE", "BYHOUR", "BYMONTHDAY", "BYMONTH", "BYSETPOS"}
_PARSE_DTSTART = datetime(2000, 1, 1)  # dateutil needs one to build the rule; only its parts are kept
_BY_PARTS = {k for k in _PARTS if k.startswith("BY")}
_MONTH_LEVEL = {"BYMONTH", "BYMONTHDAY", "BYSETPOS"}  # parts that can leave whole months out
# Wall-clock shifts (DST and the like) are assumed to stay under this. tzdata's
# future transitions are at most 1 hour (a few are 30 minutes).
_MAX_SHIFT = timedelta(hours=3)
_VALIDATION_DEADLINE = 0.5  # seconds; dateutil can search for a very long time for rules that never fire
_SAMPLE_SIZE = 3000
_SAMPLE_MONTHS = 26  # sample this far ahead, so at least 24 whole calendar months are checked
_CHECKPOINT_EVERY = 32


class RecurrenceError(ValueError):
    """An invalid rule or time zone. The message is safe to show to players."""


# -- parsing and validation -----------------------------------------------------------

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
    if freq == "SECONDLY" and _BY_PARTS & parts.keys():
        raise RecurrenceError("FREQ=SECONDLY can only be a plain interval (FREQ=SECONDLY;INTERVAL=N)")
    if "BYSETPOS" in parts and not (_BY_PARTS - {"BYSETPOS"}) & parts.keys():
        raise RecurrenceError("BYSETPOS needs another BY... part to pick from")
    _static_satisfiable(parts)
    return parts


def normalize_rule(text: Any) -> str:
    """The rule as stored: dateutil's canonical spelling, without an ``RRULE:`` prefix."""
    return ";".join(f"{k}={v}" for k, v in parse_rule(text).items())


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


def _numbered_byday(parts: dict[str, str]) -> list[int]:
    """The ordinals in BYDAY (e.g. [4, -1] for 4TH,-1FR)."""
    out = []
    for d in parts.get("BYDAY", "").split(","):
        m = _BYDAY_RE.match(d)
        if m and m.group(1):
            out.append(int(m.group(1)))
    return out


def _skip_hint(parts: dict[str, str]) -> str:
    days = [int(d) for d in parts.get("BYMONTHDAY", "").split(",") if d]
    if any(d >= 29 for d in days):
        return " Use BYMONTHDAY=-1 for the last day of every month."
    if "5" in parts.get("BYSETPOS", "").split(",") or any(abs(x) == 5 for x in _numbered_byday(parts)):
        return " Use -1 for the last of that weekday in every month (BYDAY=-1TH, or BYSETPOS=-1)."
    return ""


def _check_months(epochs: list[float], zone: ZoneInfo, ref: datetime, parts: dict[str, str]) -> None:
    """Every whole calendar month after ``ref``'s, within the sample, has an occurrence."""
    have = {(d.year, d.month) for d in (datetime.fromtimestamp(e, zone) for e in epochs)}
    y, m = ref.year, ref.month
    end = (ref.year * 12 + ref.month - 1) + _SAMPLE_MONTHS  # the horizon's month, not whole
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
    period = _pure_period(parts)
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
        epochs, full = _sample(rule, zone, ref)
        if not epochs:
            raise RecurrenceError("never fires (or fires less than once a month)")
        between += [b - a for a, b in zip(epochs, epochs[1:])]
        waits.append(epochs[0] - _epoch(ref, zone))
        if full:
            month_checks.append((epochs, ref))
        elif _MONTH_LEVEL & parts.keys() or _numbered_byday(parts):
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


def on_round_minutes(rule: str) -> bool:
    """Whether this rule's ticks (and the game's deadline) are on whole minutes.
    Only debug fast ticks (FREQ=SECONDLY) aren't."""
    return parse_rule(rule)["FREQ"] != "SECONDLY"


def round_up_to_minute(t: float) -> float:
    return math.ceil(t / 60) * 60.0


def debug_rule(tick_seconds: int) -> str:
    return f"FREQ=SECONDLY;INTERVAL={int(tick_seconds)}"


def _static_satisfiable(parts: dict[str, str]) -> None:
    """Catch the common rules that can never fire without asking dateutil, which would
    search up to the year 9999 for them."""
    months = [int(m) for m in parts["BYMONTH"].split(",")] if "BYMONTH" in parts else list(range(1, 13))
    if "BYMONTHDAY" in parts:
        days = [int(d) for d in parts["BYMONTHDAY"].split(",")]
        if not any(abs(d) <= calendar.monthrange(2024, m)[1] for d in days for m in months):  # a leap year
            raise RecurrenceError("never fires: no month in BYMONTH has that BYMONTHDAY")


def _sample(rule: str, zone: ZoneInfo, ref: datetime) -> tuple[list[float], bool]:
    """Occurrence epochs within ``_SAMPLE_MONTHS`` of ``ref`` (at most ``_SAMPLE_SIZE``),
    under a time budget, and whether they reach that horizon (False: the sample ran out first)."""
    horizon = ref + timedelta(days=_SAMPLE_MONTHS * 31)
    out: list[datetime] = []
    done = threading.Event()
    full = [True]

    def work() -> None:
        try:
            r = _compile(rule, ref.replace(second=0, microsecond=0))
            for w in r.xafter(ref, inc=False):
                if w > horizon:
                    break
                if len(out) >= _SAMPLE_SIZE:
                    full[0] = False
                    break
                out.append(w)
        except Exception:  # noqa: BLE001 - dateutil may reject a combination; treated as "never fires"
            out.clear()
        finally:
            done.set()

    # dateutil has no search bound for rules that never match, so run it under a
    # deadline. A thread that overruns finishes on its own (at most a few seconds).
    t = threading.Thread(target=work, daemon=True)
    t.start()
    if not done.wait(_VALIDATION_DEADLINE):
        raise RecurrenceError("never fires, or is too complex to check")
    return sorted({_epoch(w, zone) for w in out}), full[0]


# -- evaluation ---------------------------------------------------------------------------------

def _pure_period(parts: dict[str, str]) -> int | None:
    """Seconds between occurrences for a plain sub-daily interval, else None."""
    unit = _UNIT_SECONDS.get(parts["FREQ"])
    if unit is None or _BY_PARTS & parts.keys():
        return None
    return unit * int(parts.get("INTERVAL", "1"))


def _effective_text(parts: dict[str, str]) -> str:
    """Make implied time parts explicit (see the module docstring)."""
    p = dict(parts)
    # Only parts finer than FREQ: with FREQ=MINUTELY the minute is what repeats.
    if "BYHOUR" in p and "BYMINUTE" not in p and p["FREQ"] != "MINUTELY":
        p["BYMINUTE"] = "0"
    if p["FREQ"] != "SECONDLY":
        p.setdefault("BYSECOND", "0")  # round minutes
    return ";".join(f"{k}={v}" for k, v in p.items())


def _compile(rule: str, dtstart_wall: datetime) -> rrule:
    r = rrulestr(_effective_text(parse_rule(rule)), dtstart=dtstart_wall)
    assert isinstance(r, rrule)
    return r


def _wall(t: float, zone: ZoneInfo) -> datetime:
    """Naive local wall time of an epoch."""
    return datetime.fromtimestamp(t, zone).replace(tzinfo=None)


def _epoch(w: datetime, zone: ZoneInfo) -> float:
    """Epoch of a naive local wall time (fold=0: gap times shift forward, repeated times use the first)."""
    return w.replace(tzinfo=zone).timestamp()


class Schedule:
    """A rule bound to a zone and a start. Thread-safe; get instances from ``schedule()``,
    which caches them so the rule is parsed once."""

    def __init__(self, rule: str, tz: str, dtstart: float):
        parts = parse_rule(rule)
        self.rule, self.tz, self.dtstart = normalize_rule(rule), check_timezone(tz), float(dtstart)
        self.zone = ZoneInfo(tz)
        self.period = _pure_period(parts)
        # Round-minute rules count from the start floored to the minute (only occurrences
        # strictly after the real start happen); SECONDLY keeps the exact start.
        self.anchor = self.dtstart if parts["FREQ"] == "SECONDLY" else math.floor(self.dtstart / 60) * 60.0
        self._lock = threading.Lock()
        self._next: dict[float, float | None] = {}
        self._upcoming: dict[tuple[float, int], tuple[float, ...]] = {}
        if self.period is None:
            self._start_wall = _wall(math.floor(self.anchor), self.zone)
            self._rrule = _compile(self.rule, self._start_wall)
            # Known occurrences (wall time), sorted; iteration restarts from the nearest one
            # instead of from DTSTART. Restarting a rule at one of its own occurrences
            # keeps its phase (INTERVAL counts periods from there, and every part dateutil
            # derives from DTSTART is shared by its occurrences).
            self._checkpoints: list[datetime] = []

    def __repr__(self) -> str:
        return f"Schedule({self.rule!r}, {self.tz!r}, {self.dtstart!r})"

    # -- queries ------------------------------------------------------------------

    def iter_after(self, t: float) -> Iterator[float]:
        """Occurrences strictly after ``t``, in order, lazily."""
        if self.period is not None:
            yield from self._iter_period(t)
        else:
            yield from self._iter_rule(t)

    def next_after(self, t: float) -> float | None:
        with self._lock:
            if t in self._next:
                return self._next[t]
        nxt = next(self.iter_after(t), None)
        with self._lock:
            if len(self._next) > 512:
                self._next.clear()
            self._next[t] = nxt
        return nxt

    def upcoming(self, t: float, n: int) -> list[float]:
        """The next ``n`` occurrences after ``t`` (cached: every game view asks for them)."""
        key = (t, n)
        with self._lock:
            hit = self._upcoming.get(key)
        if hit is not None:
            return list(hit)
        out: list[float] = []
        for x in self.iter_after(t):
            out.append(x)
            if len(out) >= n:
                break
        with self._lock:
            if len(self._upcoming) > 64:
                self._upcoming.clear()
            self._upcoming[key] = tuple(out)
        return out

    def between(self, a: float, b: float, keep: int, before: float = math.inf) -> tuple[int, list[float], float | None]:
        """Occurrences in ``(a, b]`` that are also earlier than ``before``: (how many, the
        first ``keep`` of them, the last one). Memory stays bounded however many there are."""
        if self.period is not None:
            p, d = self.period, self.anchor
            first_k = math.floor((a - d) / p) + 1
            while d + first_k * p <= a:
                first_k += 1
            last_k = math.floor((min(b, before) - d) / p)
            while d + last_k * p > b or d + last_k * p >= before:
                last_k -= 1
            n = max(0, last_k - first_k + 1)
            kept = [d + k * p for k in range(first_k, first_k + min(n, keep))]
            return n, kept, (d + last_k * p) if n else None
        n, kept, last = 0, [], None
        for x in self.iter_after(a):
            if x > b or x >= before:
                break
            n += 1
            last = x
            if len(kept) < keep:
                kept.append(x)
        return n, kept, last

    # -- internals ------------------------------------------------------------------

    def _iter_period(self, t: float) -> Iterator[float]:
        p, d = self.period, self.anchor
        assert p is not None
        k = max(1, math.floor((t - d) / p) + 1)
        while d + k * p <= t:
            k += 1
        while True:
            yield d + k * p
            k += 1

    def _iter_rule(self, t: float) -> Iterator[float]:
        zone = self.zone
        # Anything with a wall time before this is before t (shifts are < _MAX_SHIFT).
        from_wall = max(self._start_wall, _wall(t, zone) - _MAX_SHIFT)
        with self._lock:
            i = bisect.bisect_right(self._checkpoints, from_wall)
            anchor = self._checkpoints[i - 1] if i else None
        r = self._rrule if anchor is None else self._rrule.replace(dtstart=anchor)
        heap: list[float] = []
        last = -math.inf
        seen = 0
        for w in r.xafter(from_wall if anchor is None or anchor < from_wall else anchor, inc=True):
            if w < self._start_wall:
                continue
            seen += 1
            if seen % _CHECKPOINT_EVERY == 0:
                self._remember(w)
            heapq.heappush(heap, _epoch(w, zone))
            # Later wall times can only map to instants at most _MAX_SHIFT earlier than this one.
            safe = _epoch(w, zone) - _MAX_SHIFT.total_seconds()
            while heap and heap[0] < safe:
                x = heapq.heappop(heap)
                if x > t and x > last:
                    last = x
                    yield x
        while heap:  # the rule ran out (only at the end of the calendar)
            x = heapq.heappop(heap)
            if x > t and x > last:
                last = x
                yield x

    def _remember(self, w: datetime) -> None:
        with self._lock:
            i = bisect.bisect_right(self._checkpoints, w)
            if not (i and self._checkpoints[i - 1] == w):
                self._checkpoints.insert(i, w)


@lru_cache(maxsize=1024)
def schedule(rule: str, tz: str, dtstart: float) -> Schedule:
    """The (cached) schedule for a rule, zone and start."""
    return Schedule(rule, tz, dtstart)


# -- presentation ------------------------------------------------------------------------------

_DAY_NAMES = dict(zip(_WEEKDAYS, ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))
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
    period = _pure_period(p)
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
        if all(d in _WEEKDAYS for d in names):
            if sorted(names, key=_WEEKDAYS.index) == list(_WEEKDAYS[:5]) and n == 1:
                return "Every weekday (Mon–Fri)" + at
            label = ", ".join(_DAY_NAMES[d] for d in sorted(names, key=_WEEKDAYS.index))
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
    m = _BYDAY_RE.match(p["BYDAY"])
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


def preview(rule: str, tz: str, start: float, count: int = 5) -> list[dict[str, Any]]:
    """The first ``count`` occurrences of a schedule starting at ``start``, each with
    its local time (ISO 8601 with offset) and whether the UTC offset changed since the
    previous one (a DST shift)."""
    s = Schedule(rule, tz, start)  # not cached: previews start at arbitrary moments
    zone = ZoneInfo(tz)
    prev = datetime.fromtimestamp(start, zone).utcoffset()
    out = []
    for t in s.upcoming(start, count):
        local = datetime.fromtimestamp(t, zone)
        off = local.utcoffset()
        out.append({"ts": t, "local": local.isoformat(timespec="minutes" if local.second == 0 else "seconds"), "utc_offset": _offset(off),
                    "dst_shift": off != prev})
        prev = off
    return out


def _offset(off: timedelta | None) -> str:
    total = int((off or timedelta()).total_seconds() // 60)
    sign = "+" if total >= 0 else "-"
    return f"{sign}{abs(total) // 60:02d}:{abs(total) % 60:02d}"


__all__ = [
    "DEFAULT_RULE", "DEFAULT_TIMEZONE", "MAX_GAP_SECONDS", "MIN_GAP_SECONDS", "RecurrenceError", "RuleStats",
    "Schedule", "check_timezone", "debug_rule", "describe", "normalize_rule", "on_round_minutes",
    "parse_rule", "preview", "round_up_to_minute", "rule_stats", "schedule", "timezones", "validate",
]
