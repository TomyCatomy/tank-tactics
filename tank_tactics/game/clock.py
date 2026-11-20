"""Time-driven events: AP ticks, jury cycle closes and the optional time limit."""
from __future__ import annotations

import random

from .board import living_players
from .events import gain_ap, log_event
from .jury import close_jury_cycle
from .outcome import end_by_time_limit
from .state import EventType, GameState, LogEntry

# Above this many consecutive pending AP ticks, catch-up logs one combined entry.
MAX_INDIVIDUAL_TICK_ENTRIES = 50
_INF = float("inf")


def next_ap_tick_at(state: GameState) -> float | None:
    if state.status != "active" or state.last_ap_tick_at is None:
        return None
    return state.last_ap_tick_at + state.config.ap_interval


def next_jury_close_at(state: GameState) -> float | None:
    if state.status != "active" or not state.config.jury.enabled or state.last_jury_cycle_at is None:
        return None
    return state.last_jury_cycle_at + state.config.jury_interval


def game_ends_at(state: GameState) -> float | None:
    d = state.config.max_duration_seconds
    if state.status != "active" or d is None or state.started_at is None:
        return None
    return state.started_at + d


def next_due_at(state: GameState) -> float | None:
    """The earliest moment ``advance_time`` has work to do, or None."""
    times = [t for t in (next_ap_tick_at(state), next_jury_close_at(state), game_ends_at(state)) if t is not None]
    return min(times) if times else None


def advance_time(state: GameState, now: float, rng: random.Random) -> list[LogEntry]:
    """Apply every AP tick, jury close and time limit due at or before ``now``, in
    chronological order. Used by the live scheduler and for catch-up after server
    downtime (it applies however many whole intervals have elapsed)."""
    entries: list[LogEntry] = []
    while state.status == "active":
        ap_at = next_ap_tick_at(state)
        assert ap_at is not None and state.last_ap_tick_at is not None
        jury_next, end_next = next_jury_close_at(state), game_ends_at(state)
        jury_at = _INF if jury_next is None else jury_next
        end_at = _INF if end_next is None else end_next
        t = min(ap_at, jury_at, end_at)
        if t > now:
            break
        if ap_at == t and ap_at < end_at:
            entries += _apply_ap_ticks(state, now, jury_at, end_at)
        elif jury_at == t and jury_at < end_at:
            entries += close_jury_cycle(state, jury_at, rng)
        else:
            entries += end_by_time_limit(state, end_at)
    return entries


def _apply_ap_ticks(state: GameState, now: float, jury_at: float, end_at: float) -> list[LogEntry]:
    """All AP ticks due before the next non-AP event, applied together.
    A tick at the exact same moment as a jury close happens first."""
    assert state.last_ap_tick_at is not None
    ap_int = state.config.ap_interval
    limit = min(now, jury_at, end_at - 1e-9)
    ticks = max(1, int((limit - state.last_ap_tick_at) // ap_int))
    living = living_players(state)
    recipients = [p.id for p in living]
    for p in living:
        gain_ap(state, p, ticks)
    base = state.last_ap_tick_at
    state.last_ap_tick_at = base + ticks * ap_int
    # Recipients are listed, balances are not (so entries stay public under fog).
    if ticks <= MAX_INDIVIDUAL_TICK_ENTRIES:
        return [
            log_event(state, base + i * ap_int, EventType.AP_TICK, amount=1, details={"recipients": recipients})
            for i in range(1, ticks + 1)
        ]
    return [
        log_event(state, base + ticks * ap_int, EventType.AP_TICK, amount=ticks,
                  details={"recipients": recipients, "ticks": ticks})
    ]
