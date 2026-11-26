"""Per-viewer redaction. Fog of war is enforced HERE, on the server.

Never send GameState to a client; always send ``view_for(state, viewer)``.
"""
from __future__ import annotations

import copy
from typing import Any

from .board import find_player, is_juror
from .clock import game_ends_at, next_ap_tick_at, next_jury_close_at
from .recurrence import schedule_view
from .state import GameState, LogEntry


def can_see(entry: LogEntry, viewer_id: str | None) -> bool:
    if entry.visibility == "public":
        return True
    if viewer_id is None:
        return False
    if entry.visibility == "participants":
        return viewer_id in (entry.actor_id, entry.target_id)
    return entry.actor_id == viewer_id  # actor_only


def view_for(state: GameState, viewer_id: str | None) -> dict[str, Any]:
    fog_off = state.config.fog_of_war == "off"
    viewer = find_player(state, viewer_id) if viewer_id else None
    players = []
    for p in state.players:
        pv: dict[str, Any] = {
            "id": p.id, "name": p.name, "color": p.color, "x": p.x, "y": p.y, "hp": p.hp,
            "action_range": p.action_range, "alive": p.alive, "is_host": p.is_host,
            "is_juror": is_juror(state, p), "died_at": p.died_at,
        }
        if fog_off or (viewer is not None and p.id == viewer.id):
            pv["ap"] = p.ap
        players.append(pv)

    config = state.config.model_dump()
    if viewer is None or not viewer.is_host:
        config["notifications"]["discord_webhook_url"] = None  # host only

    jury = None
    if state.config.jury.enabled:
        votes = [
            {"juror_id": v.juror_id, "candidate_id": v.candidate_id}
            for v in state.jury_votes
            if v.cycle_index == state.jury_cycle_index
            and (state.config.jury.votes_public or (viewer is not None and v.juror_id == viewer.id))
        ]
        jury = {"cycle_index": state.jury_cycle_index, "closes_at": next_jury_close_at(state), "votes": votes}

    return {
        "game": {
            "id": state.id,
            "status": state.status,
            "config": config,
            "board_size": state.board_size,
            "created_at": state.created_at,
            "started_at": state.started_at,
            "ended_at": state.ended_at,
            "winner_ids": list(state.winner_ids),
            "end_reason": state.end_reason,
            "next_ap_tick_at": next_ap_tick_at(state),
            "next_jury_close_at": next_jury_close_at(state),
            "ends_at": game_ends_at(state),
            # AP schedule (the jury closes on the same occurrences): rule, zone, summary, next ticks.
            "schedule": schedule_view(
                state, state.last_ap_tick_at if state.status == "active" else None, game_ends_at(state)),
        },
        "viewer_id": viewer.id if viewer else None,
        "players": players,
        "log": [copy.deepcopy(e.to_dict()) for e in state.log if can_see(e, viewer.id if viewer else None)],
        "jury": jury,
    }
