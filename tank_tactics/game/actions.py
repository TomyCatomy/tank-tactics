"""The five things a living tank can do: move, shoot, give AP, give HP, upgrade range.

Each validates everything first and only then mutates, so a raised GameError
never leaves the state half-changed.
"""
from __future__ import annotations

from typing import Any

from .board import adjacent, in_bounds, occupant_at
from .events import fogged, gain_ap, log_event
from .guards import (
    require_ap,
    require_in_range,
    require_living_actor,
    require_living_target,
    require_positive_int,
)
from .outcome import check_game_over, kill_tank, snapshot_ap
from .state import EventType, GameError, GameState, LogEntry, Player

MOVE_COST = 1
SHOOT_COST = 1


def move(state: GameState, actor_id: str, to: Any, now: float) -> list[LogEntry]:
    actor = require_living_actor(state, actor_id)
    try:
        dest = (to[0], to[1])
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in dest):
            raise TypeError
    except (TypeError, IndexError, KeyError):
        raise GameError("out_of_bounds", "Invalid destination.") from None
    if not in_bounds(state, dest):
        raise GameError("out_of_bounds", "That tile is off the board.")
    if not adjacent(actor.pos, dest):
        raise GameError("not_adjacent", "You can only move one tile up, down, left or right.")
    if occupant_at(state, dest):
        raise GameError("occupied", "That tile is occupied.")
    require_ap(actor, MOVE_COST)

    src = actor.pos
    actor.ap -= MOVE_COST
    actor.x, actor.y = dest
    return [log_event(state, now, EventType.MOVE, actor_id=actor_id, from_xy=src, to_xy=dest)]


def shoot(state: GameState, actor_id: str, target_id: Any, now: float) -> list[LogEntry]:
    actor = require_living_actor(state, actor_id)
    target = require_living_target(state, actor, target_id)
    require_in_range(actor, target)
    require_ap(actor, SHOOT_COST)

    ap_before = snapshot_ap(state)
    actor.ap -= SHOOT_COST
    target.hp -= 1
    entries = [
        log_event(
            state, now, EventType.SHOOT, actor_id=actor_id, target_id=target.id, amount=1,
            from_xy=actor.pos, to_xy=target.pos, details={"target_hp_after": max(0, target.hp)},
        )
    ]
    if target.hp <= 0:
        loot = target.ap
        target.ap = 0
        kill_tank(state, target, now)
        entries.append(log_event(state, now, EventType.KILL, actor_id=actor_id, target_id=target.id, to_xy=target.pos))
        if state.config.actions.loot_on_kill:
            received = gain_ap(state, actor, loot)
            # The kill is public; the loot amount is visible only to the killer under alliance fog.
            entries.append(
                log_event(
                    state, now, EventType.LOOT, actor_id=actor_id, target_id=target.id, amount=received,
                    details={"looted": loot} if received != loot else {},
                    visibility=fogged(state, "actor_only"),
                )
            )
        entries += check_game_over(state, now, ap_before)
    return entries


def give_ap(state: GameState, actor_id: str, target_id: Any, amount: Any, now: float) -> list[LogEntry]:
    actor = require_living_actor(state, actor_id)
    target = require_living_target(state, actor, target_id)
    n = require_positive_int(amount, "AP amount")
    require_in_range(actor, target)
    require_ap(actor, n)

    actor.ap -= n
    received = gain_ap(state, target, n)
    return [
        log_event(
            state, now, EventType.GIVE_AP, actor_id=actor_id, target_id=target.id, amount=n,
            details={"received": received} if received != n else {},
            visibility=fogged(state, "participants"),
        )
    ]


def give_hp(state: GameState, actor_id: str, target_id: Any, amount: Any, now: float) -> list[LogEntry]:
    actor = require_living_actor(state, actor_id)
    if not state.config.actions.allow_give_hp:
        raise GameError("disabled", "Giving HP is disabled in this game.")
    target = require_living_target(state, actor, target_id)
    n = require_positive_int(amount, "HP amount")
    require_in_range(actor, target)
    if actor.hp - n < 1:
        raise GameError("insufficient_hp", f"You must keep at least 1 HP (you have {actor.hp}).")

    actor.hp -= n
    target.hp += n
    return [
        log_event(
            state, now, EventType.GIVE_HP, actor_id=actor_id, target_id=target.id, amount=n,
            visibility=fogged(state, "participants"),
        )
    ]


def range_upgrade_cost(state: GameState, player: Player) -> int:
    """What `player`'s next range upgrade costs: the base cost, plus the step for every upgrade
    already bought (range is only ever gained by upgrading, so that count is range - starting range)."""
    rules = state.config.actions
    bought = max(0, player.action_range - state.config.start.action_range)
    return rules.range_upgrade_cost + rules.range_upgrade_step * bought


def upgrade_range(state: GameState, actor_id: str, now: float) -> list[LogEntry]:
    actor = require_living_actor(state, actor_id)
    cost, max_range = range_upgrade_cost(state, actor), state.config.actions.max_range
    if max_range is not None and actor.action_range >= max_range:
        raise GameError("max_range", f"Your range is already at the maximum ({max_range}).")
    require_ap(actor, cost)

    actor.ap -= cost
    actor.action_range += 1
    return [log_event(state, now, EventType.UPGRADE, actor_id=actor_id, amount=actor.action_range, details={"cost": cost})]
