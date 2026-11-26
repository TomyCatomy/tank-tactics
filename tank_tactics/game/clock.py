"""Time-driven events: AP ticks, jury cycle closes and the optional time limit.

AP ticks happen on each occurrence of the game's recurrence rule (``ap.rrule`` in
``ap.timezone``, DTSTART = start; see ``recurrence``). With the jury on, a jury cycle
closes on each of the same occurrences, just after that occurrence's AP tick.
"""
from __future__ import annotations

import random

from .board import living_players
from .events import gain_ap, log_event
from .jury import close_jury_cycle
from .outcome import end_by_time_limit
from .. import recurrence
from .recurrence import next_occurrence_after, occurrences_between
from .state import EventType, GameState, LogEntry

# Above this many consecutive pending AP ticks, catch-up logs one combined entry.
MAX_INDIVIDUAL_TICK_ENTRIES = 50
_INF = float("inf")


def next_ap_tick_at(state: GameState) -> float | None:
    if state.status != "active" or state.last_ap_tick_at is None:
        return None
    return next_occurrence_after(state, state.last_ap_tick_at)


def next_jury_close_at(state: GameState) -> float | None:
    if state.status != "active" or not state.config.jury.enabled or state.last_jury_cycle_at is None:
        return None
    return next_occurrence_after(state, state.last_jury_cycle_at)


def game_ends_at(state: GameState) -> float | None:
    """The time limit: ``max_duration_seconds`` after the start, rounded up to the next
    round minute like every other scheduled event (exact for debug fast ticks). Nothing else happens at that exact moment (NOTES.md 7)."""
    d = state.config.max_duration_seconds
    if state.status != "active" or d is None or state.started_at is None:
        return None
    end = state.started_at + d
    rule, _ = state.config.effective_schedule
    return recurrence.round_up_to_minute(end) if recurrence.on_round_minutes(rule) else end


def next_due_at(state: GameState) -> float | None:
    """The earliest moment ``advance_time`` has work to do, or None."""
    times = [t for t in (next_ap_tick_at(state), next_jury_close_at(state), game_ends_at(state)) if t is not None]
    return min(times) if times else None


def advance_time(state: GameState, now: float, rng: random.Random) -> list[LogEntry]:
    """Apply every AP tick, jury close and time limit due at or before ``now``, in
    chronological order. Used by the live scheduler and for catch-up after server
    downtime (it applies every occurrence that has passed)."""
    entries: list[LogEntry] = []
    while state.status == "active":
        ap_next, jury_next, end_next = next_ap_tick_at(state), next_jury_close_at(state), game_ends_at(state)
        ap_at = _INF if ap_next is None else ap_next
        jury_at = _INF if jury_next is None else jury_next
        end_at = _INF if end_next is None else end_next
        t = min(ap_at, jury_at, end_at)
        if t > now:
            break
        # At the same instant: AP tick, then jury close; neither happens at the time limit.
        if ap_at == t and ap_at < end_at:
            entries += _apply_ap_ticks(state, now, jury_at, end_at)
        elif jury_at == t and jury_at < end_at:
            entries += close_jury_cycle(state, jury_at, rng)
        else:
            entries += end_by_time_limit(state, end_at)
    return entries


def _apply_ap_ticks(state: GameState, now: float, jury_at: float, end_at: float) -> list[LogEntry]:
    """All AP ticks due before the next non-AP event, applied together.
    A tick at the exact same moment as a jury close happens first; a tick at the
    exact moment of the time limit doesn't happen."""
    assert state.last_ap_tick_at is not None
    n, first, last = occurrences_between(
        state, state.last_ap_tick_at, min(now, jury_at), MAX_INDIVIDUAL_TICK_ENTRIES, before=end_at)
    assert n >= 1 and last is not None, "called only when a tick is due"
    living = living_players(state)
    recipients = [p.id for p in living]
    for p in living:
        gain_ap(state, p, n)
    state.last_ap_tick_at = last
    # Recipients are listed, balances are not (so entries stay public under fog).
    if n <= MAX_INDIVIDUAL_TICK_ENTRIES:
        return [log_event(state, t, EventType.AP_TICK, amount=1, details={"recipients": recipients}) for t in first]
    return [log_event(state, last, EventType.AP_TICK, amount=n, details={"recipients": recipients, "ticks": n})]
