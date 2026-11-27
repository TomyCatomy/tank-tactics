"""Validation checks shared by actions. Each raises GameError and never mutates."""
from __future__ import annotations

from typing import Any

from .board import find_player, get_player, in_range, range_distance
from .state import GameError, GameState, Player


CLOSED_MESSAGE = "The host closed this lobby."


def require_lobby(state: GameState) -> None:
    if state.status == "closed":
        raise GameError("closed", CLOSED_MESSAGE)
    if state.status != "lobby":
        raise GameError("not_lobby", "The game has already started.")


def require_active(state: GameState) -> None:
    if state.status == "closed":
        raise GameError("closed", CLOSED_MESSAGE)
    if state.status != "active":
        msg = "The game has not started yet." if state.status == "lobby" else "The game is over."
        raise GameError("not_active", msg)


def require_host(state: GameState, actor_id: str) -> Player:
    p = get_player(state, actor_id)
    if not p.is_host:
        raise GameError("forbidden", "Only the host can do that.")
    return p


def require_living_actor(state: GameState, actor_id: str) -> Player:
    require_active(state)
    actor = get_player(state, actor_id)
    if not actor.alive:
        raise GameError("dead", "Your tank has been destroyed.")
    return actor


def require_living_target(state: GameState, actor: Player, target_id: Any) -> Player:
    target = find_player(state, target_id) if isinstance(target_id, str) else None
    if target is None:
        raise GameError("not_found", "No such tank.")
    if target.id == actor.id:
        raise GameError("invalid_target", "You cannot target yourself.")
    if not target.alive:
        raise GameError("invalid_target", "That tank is already destroyed.")
    return target


def require_in_range(actor: Player, target: Player) -> None:
    if not in_range(actor, target.pos):
        raise GameError(
            "out_of_range",
            f"Target is out of range (distance {range_distance(actor.pos, target.pos):.1f}, your range {actor.action_range}).",
        )


def require_positive_int(n: Any, what: str) -> int:
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise GameError("invalid_amount", f"{what} must be a whole number of at least 1.")
    return n


def require_ap(actor: Player, cost: int) -> None:
    if actor.ap < cost:
        raise GameError("insufficient_ap", f"Not enough AP (need {cost}, you have {actor.ap}).")
