"""Game setup: creating a game, joining, settings, removing players and starting."""
from __future__ import annotations

import random
from typing import Any

from ..config import GameConfig
from .board import distance, get_player
from .colors import next_free_color
from .events import log_event
from .guards import require_active, require_host, require_lobby
from .outcome import check_game_over, kill_tank, snapshot_ap
from .state import EventType, GameError, GameState, LogEntry, Player

def _normalize_name(raw: Any) -> str:
    if not isinstance(raw, str):
        raise GameError("invalid_name", "Name is required.")
    name = " ".join(raw.split())
    if not 1 <= len(name) <= 24:
        raise GameError("invalid_name", "Name must be 1-24 characters.")
    return name


def create_game(game_id: str, host_id: str, host_name: str, config: GameConfig, now: float) -> GameState:
    state = GameState(id=game_id, config=config, created_at=now, board_size=config.board.size_for(1))
    add_player(state, host_id, host_name, now, is_host=True)
    return state


def add_player(state: GameState, player_id: str, name: Any, now: float, is_host: bool = False) -> list[LogEntry]:
    require_lobby(state)
    name = _normalize_name(name)
    if len(state.players) >= state.config.players.maximum:
        raise GameError("lobby_full", "This game is full.")
    if any(p.name.lower() == name.lower() for p in state.players):
        raise GameError("invalid_name", "That name is already taken in this game.")
    color = next_free_color(state)
    c = state.config
    state.players.append(
        Player(
            id=player_id, name=name, color=color, hp=c.start.hp, ap=c.start.ap,
            action_range=c.start.action_range, is_host=is_host,
        )
    )
    return [log_event(state, now, EventType.JOIN, actor_id=player_id, details={"name": name})]


def update_config(state: GameState, actor_id: str, patch: Any, now: float) -> list[LogEntry]:
    require_lobby(state)
    require_host(state, actor_id)
    new = state.config.with_overrides(patch)  # raises ConfigError
    if new.players.maximum < len(state.players):
        raise GameError("lobby_full", "max_players is lower than the number of players already joined.")
    state.config = new
    for p in state.players:
        p.hp, p.ap, p.action_range = new.start.hp, new.start.ap, new.start.action_range
    return [log_event(state, now, EventType.CONFIG, actor_id=actor_id)]


def remove_player(state: GameState, actor_id: str, player_id: str, now: float) -> list[LogEntry]:
    """Host removes a player. In the lobby they just leave; mid-game their tank is
    destroyed (no loot, since nobody shot it) and they become a spectator or juror."""
    require_host(state, actor_id)
    target = get_player(state, player_id)
    if target.id == actor_id:
        raise GameError("invalid_target", "The host cannot remove themselves.")
    if state.status == "lobby":
        state.players = [p for p in state.players if p.id != player_id]
        return [log_event(state, now, EventType.LEAVE, actor_id=player_id, details={"name": target.name, "removed_by": actor_id})]
    require_active(state)
    if not target.alive:
        raise GameError("invalid_target", "That tank is already destroyed.")
    ap_before = snapshot_ap(state)
    target.ap = 0
    kill_tank(state, target, now)
    entries = [log_event(state, now, EventType.REMOVED, actor_id=actor_id, target_id=target.id, to_xy=target.pos)]
    entries += check_game_over(state, now, ap_before)
    return entries


def leave_lobby(state: GameState, player_id: str, now: float) -> list[LogEntry]:
    """A player leaves before the game starts. The host can't leave (they close the lobby instead)."""
    require_lobby(state)
    player = get_player(state, player_id)
    if player.is_host:
        raise GameError("forbidden", "The host can't leave the lobby; close it instead.")
    state.players = [p for p in state.players if p.id != player_id]
    return [log_event(state, now, EventType.LEAVE, actor_id=player_id, details={"name": player.name})]


def close_lobby(state: GameState, actor_id: str, now: float) -> list[LogEntry]:
    """The host cancels the game before it starts. Final: nobody can join or start it again."""
    require_lobby(state)
    require_host(state, actor_id)
    state.status = "closed"
    state.ended_at = now
    return [log_event(state, now, EventType.LOBBY_CLOSED, actor_id=actor_id)]


def place_tanks(board_size: int, count: int, rng: random.Random) -> list[tuple[int, int]]:
    """Random unique tiles, preferring tanks >= 3 apart (not within distance 2),
    then >= 2 apart (not within distance 1), then any free tile."""
    if count > board_size * board_size:
        raise GameError("board_too_small", "The board is too small for this many players.")
    tiles = [(x, y) for y in range(board_size) for x in range(board_size)]
    for min_dist in (3, 2, 1):
        for _attempt in range(50):
            rng.shuffle(tiles)
            placed: list[tuple[int, int]] = []
            for t in tiles:
                if all(distance(p, t) >= min_dist for p in placed):
                    placed.append(t)
                    if len(placed) == count:
                        return placed
    raise AssertionError("unreachable: min_dist 1 always succeeds")


def start_game(state: GameState, actor_id: str, now: float, rng: random.Random) -> list[LogEntry]:
    require_lobby(state)
    require_host(state, actor_id)
    c = state.config
    needed = max(2, c.players.minimum)
    if len(state.players) < needed:
        raise GameError("not_enough_players", f"Need at least {needed} players to start.")
    size = c.board.size_for(len(state.players))
    spots = place_tanks(size, len(state.players), rng)
    state.board_size = size
    for p, (x, y) in zip(state.players, spots):
        p.x, p.y = x, y
        p.hp, p.ap, p.action_range, p.alive = c.start.hp, c.start.ap, c.start.action_range, True
    state.status = "active"
    state.started_at = now
    state.last_ap_tick_at = now
    state.last_jury_cycle_at = now if c.jury.enabled else None
    state.jury_cycle_index = 0
    return [
        log_event(
            state, now, EventType.GAME_START,
            details={
                "board_size": size,
                "positions": {p.id: [p.x, p.y] for p in state.players},
                "hp": c.start.hp,
                "action_range": c.start.action_range,
            },
        )
    ]
