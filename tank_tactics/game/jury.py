"""The optional jury of dead players (only active when ``jury_enabled``)."""
from __future__ import annotations

import random
from typing import Any

from .board import find_player, get_player, is_juror
from .events import gain_ap, log_event
from .guards import require_active
from .state import EventType, GameError, GameState, JuryVote, LogEntry


def cast_jury_vote(state: GameState, juror_id: str, candidate_id: Any, now: float) -> list[LogEntry]:
    if not state.config.jury.enabled:
        raise GameError("disabled", "The jury is not enabled in this game.")
    require_active(state)
    juror = get_player(state, juror_id)
    if not is_juror(state, juror):
        raise GameError("forbidden", "Only dead players (jurors) can vote.")
    candidate = find_player(state, candidate_id) if isinstance(candidate_id, str) else None
    if candidate is None:
        raise GameError("not_found", "No such player.")
    if not candidate.alive:
        raise GameError("invalid_target", "You can only vote for a living player.")

    # One vote per juror per cycle; voting again replaces the earlier vote.
    cycle = state.jury_cycle_index
    state.jury_votes = [v for v in state.jury_votes if not (v.cycle_index == cycle and v.juror_id == juror_id)]
    state.jury_votes.append(JuryVote(cycle, juror_id, candidate.id, now))
    return [
        log_event(
            state, now, EventType.JURY_VOTE, actor_id=juror_id, target_id=candidate.id, details={"cycle": cycle},
            visibility="public" if state.config.jury.votes_public else "actor_only",
        )
    ]


def close_jury_cycle(state: GameState, at: float, rng: random.Random) -> list[LogEntry]:
    """Tally the current cycle, award +1 AP to the winner(s), and open the next cycle."""
    cycle = state.jury_cycle_index
    counts: dict[str, int] = {}
    for v in state.jury_votes:
        if v.cycle_index != cycle:
            continue
        cand = find_player(state, v.candidate_id)
        if cand is None or not cand.alive:
            continue  # votes for players who died during the cycle don't count
        counts[v.candidate_id] = counts.get(v.candidate_id, 0) + 1
    entries: list[LogEntry] = []
    if counts:
        top = max(counts.values())
        winners = [pid for pid, n in counts.items() if n == top]
        if len(winners) > 1 and state.config.jury.tie_rule == "random":
            winners = [rng.choice(winners)]
        for pid in winners:
            gain_ap(state, get_player(state, pid), 1)
        details: dict[str, Any] = {"cycle": cycle, "winners": winners}
        if state.config.jury.votes_public:
            details["tally"] = counts
        entries.append(log_event(state, at, EventType.JURY_AWARD, amount=1, details=details))
    state.jury_votes = [v for v in state.jury_votes if v.cycle_index > cycle]
    state.jury_cycle_index = cycle + 1
    state.last_jury_cycle_at = at
    return entries
