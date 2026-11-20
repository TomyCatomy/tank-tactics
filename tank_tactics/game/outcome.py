"""Death and winning."""
from __future__ import annotations

from .board import living_players
from .events import log_event
from .state import EventType, GameState, LogEntry, Player


def kill_tank(state: GameState, p: Player, now: float) -> None:
    """Mark a tank destroyed. With the jury on, the player becomes a juror."""
    p.alive = False
    p.hp = 0
    p.died_at = now
    if state.config.jury.enabled:
        p.joined_jury_at = now


def snapshot_ap(state: GameState) -> dict[str, int]:
    """Everyone's AP right now, for the simultaneous-death tiebreak."""
    return {p.id: p.ap for p in state.players}


def finish(state: GameState, now: float, winner_ids: list[str], reason: str) -> list[LogEntry]:
    state.status = "finished"
    state.ended_at = now
    state.winner_ids = winner_ids
    state.end_reason = reason
    # No AP numbers here, so AP tiebreaks don't leak balances under fog.
    return [log_event(state, now, EventType.GAME_OVER, details={"winners": winner_ids, "reason": reason})]


def check_game_over(state: GameState, now: float, ap_before: dict[str, int]) -> list[LogEntry]:
    """Exactly one living tank wins. If the last tanks all died at the same moment
    (safety only), whoever held the most AP at that moment wins; ties share it."""
    if state.status != "active":
        return []
    living = living_players(state)
    if len(living) == 1:
        return finish(state, now, [living[0].id], "last_standing")
    if not living:
        pool = [p for p in state.players if p.died_at == now] or state.players
        best = max(ap_before.get(p.id, 0) for p in pool)
        return finish(state, now, [p.id for p in pool if ap_before.get(p.id, 0) == best], "simultaneous")
    return []


def end_by_time_limit(state: GameState, at: float) -> list[LogEntry]:
    """Highest HP wins, then highest AP, then highest range; remaining ties share the win."""
    pool = living_players(state)
    for attr in ("hp", "ap", "action_range"):
        best = max(getattr(p, attr) for p in pool)
        pool = [p for p in pool if getattr(p, attr) == best]
    return finish(state, at, [p.id for p in pool], "time_limit")
