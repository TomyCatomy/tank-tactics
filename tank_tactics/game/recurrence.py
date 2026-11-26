"""The game's AP schedule: the configured recurrence rule bound to the game's start.

Rule parsing and occurrence maths live in ``tank_tactics.recurrence``; this module
only answers schedule questions about a ``GameState``. Schedules are cached there, so
the rule is parsed once per game, not once per call.
"""
from __future__ import annotations

from typing import Any

from .. import recurrence
from .state import GameState


def schedule_of(state: GameState) -> recurrence.Schedule | None:
    """The schedule AP ticks and jury closes follow, or None before the game starts.
    DTSTART is the start time; debug.fast_ticks replaces the rule (see GameConfig)."""
    if state.started_at is None:
        return None
    rule, tz = state.config.effective_schedule
    return recurrence.schedule(rule, tz, state.started_at)


def next_occurrence_after(state: GameState, t: float) -> float | None:
    s = schedule_of(state)
    return None if s is None else s.next_after(t)


def occurrences_between(
    state: GameState, a: float, b: float, keep: int, before: float = float("inf")
) -> tuple[int, list[float], float | None]:
    """Occurrences in ``(a, b]`` earlier than ``before``: (count, the first ``keep``, the
    last), without listing them all."""
    s = schedule_of(state)
    return (0, [], None) if s is None else s.between(a, b, keep, before)


def schedule_view(state: GameState, after: float | None, until: float | None, count: int = 5) -> dict[str, Any]:
    """What the client shows: the rule, zone, a summary and the next few occurrences
    (after ``after``, and before ``until``, the time limit, when there is one)."""
    c = state.config
    rule, tz = c.effective_schedule
    upcoming: list[float] = []
    s = schedule_of(state)
    if s is not None and after is not None:
        upcoming = [t for t in s.upcoming(after, count) if until is None or t < until]
    return {
        "rrule": c.ap.rrule,
        "timezone": c.ap.timezone,
        "summary": recurrence.describe(c.ap.rrule, c.ap.timezone),
        "effective_rrule": rule,
        "effective_timezone": tz,
        "effective_summary": recurrence.describe(rule, tz) + (" (debug fast ticks)" if c.debug.fast_ticks else ""),
        "upcoming": upcoming,
    }
