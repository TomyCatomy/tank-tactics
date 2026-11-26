"""RFC 5545 recurrence rules (RRULE) for the AP schedule.

Pure and self-contained: no clock, no I/O, no imports from the rest of the project,
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

Parsing and expansion are dateutil's (``rrulestr``); this package adds the game's
policy on top. ``FREQ=SECONDLY;INTERVAL=N`` only exists for debug fast ticks, and keeps
their exact timing (DTSTART = the exact start, no rounding).

Modules: ``rules`` (parsing and what a rule may contain), ``zones`` (time zones and
wall-clock conversions), ``cadence`` (how often a rule may fire), ``schedule`` (a rule
bound to a game's start) and ``describe`` (one-line summaries).
"""
from .cadence import MAX_GAP_SECONDS, MIN_GAP_SECONDS, RuleStats, rule_stats, validate
from .describe import describe
from .rules import (
    DEFAULT_RULE, DEFAULT_TIMEZONE, RecurrenceError, debug_rule, normalize_rule, on_round_minutes, parse_rule,
)
from .schedule import Schedule, schedule
from .zones import check_timezone, round_up_to_minute, timezones

__all__ = [
    "DEFAULT_RULE", "DEFAULT_TIMEZONE", "MAX_GAP_SECONDS", "MIN_GAP_SECONDS", "RecurrenceError", "RuleStats",
    "Schedule", "check_timezone", "debug_rule", "describe", "normalize_rule", "on_round_minutes",
    "parse_rule", "round_up_to_minute", "rule_stats", "schedule", "timezones", "validate",
]
