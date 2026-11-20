"""Low-level state changes every rule uses: appending to the log and gaining AP."""
from __future__ import annotations

from typing import Any

from .state import EventType, GameState, LogEntry, Player, Visibility


def log_event(state: GameState, ts: float, event_type: EventType, **fields: Any) -> LogEntry:
    """Append an immutable log entry. Its ``visibility`` is fixed at write time."""
    entry = LogEntry(id=state.next_log_id, ts=ts, event_type=event_type, **fields)
    state.next_log_id += 1
    state.log.append(entry)
    return entry


def fogged(state: GameState, level: Visibility) -> Visibility:
    """Visibility for information that alliance fog hides (gifts, loot amounts)."""
    return "public" if state.config.fog_of_war == "off" else level


def gain_ap(state: GameState, p: Player, amount: int) -> int:
    """Add AP respecting ``ap.cap``. Returns the amount actually added."""
    cap = state.config.ap.cap
    before = p.ap
    p.ap = p.ap + amount if cap is None else min(cap, p.ap + amount)
    return p.ap - before
