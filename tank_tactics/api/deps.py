"""FastAPI dependencies: the game service and the authenticated player."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..service import GameService

_bearer = HTTPBearer(auto_error=False, description="The player token returned by create/join.")


def get_service(request: Request) -> GameService:
    return request.app.state.service


ServiceDep = Annotated[GameService, Depends(get_service)]


@dataclass(frozen=True)
class PlayerContext:
    service: GameService
    game_id: str
    player_id: str


def current_player(
    game_id: str,
    service: ServiceDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> PlayerContext:
    """The player whose ``Authorization: Bearer <token>`` belongs to ``game_id`` (401 otherwise)."""
    player_id = service.authenticate(game_id, creds.credentials if creds else None)
    return PlayerContext(service, game_id, player_id)


PlayerDep = Annotated[PlayerContext, Depends(current_player)]
