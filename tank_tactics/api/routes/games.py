"""Creating, finding and joining games, and reading a player's view of one."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...config import GameConfig
from ..deps import OptionalUserDep, PlayerDep, ServiceDep
from ..schemas import CreateGameRequest, Credentials, GameInfo, JoinRequest

router = APIRouter(prefix="/api", tags=["games"])


@router.get("/defaults")
def defaults(service: ServiceDep) -> dict[str, Any]:
    """The default game rules (what the create form starts from)."""
    return {"config": GameConfig().model_dump(), "allow_debug": service.cfg.allow_debug}


@router.post("/games", status_code=201)
def create_game(body: CreateGameRequest, service: ServiceDep, user: OptionalUserDep) -> Credentials:
    """Create a game; the caller becomes its host. With a session token (optional),
    the seat is linked to the signed-in account."""
    return Credentials(**service.create_game(body.name, body.config, user.id if user else None))


@router.get("/games/{game_id}")
def game_info(game_id: str, service: ServiceDep) -> GameInfo:
    """Public lobby info for the invite page. Needs no token."""
    return GameInfo(**service.public_info(game_id))


@router.post("/games/{game_id}/join", status_code=201)
def join_game(game_id: str, body: JoinRequest, service: ServiceDep, user: OptionalUserDep) -> Credentials:
    """Join a game that hasn't started yet. With a session token (optional), the seat
    is linked to the signed-in account, which can hold one seat per game."""
    return Credentials(**service.join_game(game_id, body.name, user.id if user else None))


@router.get("/games/{game_id}/state")
def game_state(player: PlayerDep) -> dict[str, Any]:
    """The caller's fog-of-war-redacted view (the same payload the WebSocket pushes)."""
    return player.service.view(player.game_id, player.player_id)
