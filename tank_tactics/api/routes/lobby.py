"""Lobby management: settings, start, remove (host), leave (players), close (host)."""
from __future__ import annotations

from fastapi import APIRouter

from ..deps import PlayerDep
from ..schemas import ConfigUpdateRequest, Ok, RemovePlayerRequest

router = APIRouter(prefix="/api/games/{game_id}", tags=["lobby"])


@router.post("/config")
def update_config(body: ConfigUpdateRequest, player: PlayerDep) -> Ok:
    """Host only, before the game starts. Partial, e.g. ``{"jury": {"enabled": true}}``."""
    player.service.update_config(player.game_id, player.player_id, body.config)
    return Ok()


@router.post("/start")
def start_game(player: PlayerDep) -> Ok:
    """Host only. Needs at least two players."""
    player.service.start_game(player.game_id, player.player_id)
    return Ok()


@router.post("/leave")
def leave(player: PlayerDep) -> Ok:
    """Leave the lobby (not the host, who closes it instead). Your token stops working."""
    player.service.leave_game(player.game_id, player.player_id)
    return Ok()


@router.post("/close")
def close(player: PlayerDep) -> Ok:
    """Host only, before the game starts: cancel the game for everyone."""
    player.service.close_game(player.game_id, player.player_id)
    return Ok()


@router.post("/remove")
def remove_player(body: RemovePlayerRequest, player: PlayerDep) -> Ok:
    """Host only. In the lobby the player leaves; mid-game their tank is destroyed."""
    player.service.remove_player(player.game_id, player.player_id, body.player_id)
    return Ok()
