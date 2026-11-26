"""A rule bound to a zone and a game's start: its occurrences as epoch seconds."""
from __future__ import annotations

import bisect
import heapq
import itertools
import math
import threading
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Iterator
from zoneinfo import ZoneInfo

from .rules import compile_rule, normalize_rule, parse_rule, pure_period
from .zones import check_timezone, epoch, wall

# Wall-clock shifts (DST and the like) are assumed to stay under this. tzdata's
# future transitions are at most 1 hour (a few are 30 minutes).
_MAX_SHIFT = timedelta(hours=3)
_CHECKPOINT_EVERY = 32


class Schedule:
    """A rule bound to a zone and a start. Thread-safe; get instances from ``schedule()``,
    which caches them so the rule is parsed once."""

    def __init__(self, rule: str, tz: str, dtstart: float):
        parts = parse_rule(rule)
        self.rule, self.tz, self.dtstart = normalize_rule(rule), check_timezone(tz), float(dtstart)
        self.zone = ZoneInfo(tz)
        self.period = pure_period(parts)
        # Round-minute rules count from the start floored to the minute (only occurrences
        # strictly after the real start happen); SECONDLY keeps the exact start.
        self.anchor = self.dtstart if parts["FREQ"] == "SECONDLY" else math.floor(self.dtstart / 60) * 60.0
        self._lock = threading.Lock()
        self._next: dict[float, float | None] = {}
        if self.period is None:
            self._start_wall = wall(math.floor(self.anchor), self.zone)
            self._rrule = compile_rule(self.rule, self._start_wall)
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
        """The next ``n`` occurrences after ``t``."""
        return list(itertools.islice(self.iter_after(t), n))

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
        from_wall = max(self._start_wall, wall(t, zone) - _MAX_SHIFT)
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
            heapq.heappush(heap, epoch(w, zone))
            # Later wall times can only map to instants at most _MAX_SHIFT earlier than this one.
            safe = epoch(w, zone) - _MAX_SHIFT.total_seconds()
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
