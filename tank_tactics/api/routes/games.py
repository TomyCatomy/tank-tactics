"""Creating, finding and joining games, and reading a player's view of one."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...config import GameConfig
from ..deps import PlayerDep, ServiceDep
from ..schemas import CreateGameRequest, Credentials, GameInfo, JoinRequest

router = APIRouter(prefix="/api", tags=["games"])


@router.get("/defaults")
def defaults() -> dict[str, Any]:
    """The default game rules (what the create form starts from)."""
    return {"config": GameConfig().model_dump()}


@router.post("/games", status_code=201)
def create_game(body: CreateGameRequest, service: ServiceDep) -> Credentials:
    """Create a game; the caller becomes its host."""
    return Credentials(**service.create_game(body.name, body.config))


@router.get("/games/{game_id}")
def game_info(game_id: str, service: ServiceDep) -> GameInfo:
    """Public lobby info for the invite page. Needs no token."""
    return GameInfo(**service.public_info(game_id))


@router.post("/games/{game_id}/join", status_code=201)
def join_game(game_id: str, body: JoinRequest, service: ServiceDep) -> Credentials:
    """Join a game that hasn't started yet."""
    return Credentials(**service.join_game(game_id, body.name))


@router.get("/games/{game_id}/state")
def game_state(player: PlayerDep) -> dict[str, Any]:
    """The caller's fog-of-war-redacted view (the same payload the WebSocket pushes)."""
    return player.service.view(player.game_id, player.player_id)
