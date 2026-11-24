"""FastAPI dependencies: the game service, the authenticated player, and the
signed-in account (for the optional Google sign-in)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..service import GameService, ServiceError
from ..store import User

_bearer = HTTPBearer(auto_error=False, description="The player token returned by create/join.")
_session_bearer = HTTPBearer(
    auto_error=False, scheme_name="SessionBearer", description="The session token returned by /api/auth/google."
)


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


def session_token(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_session_bearer)]) -> str:
    if creds is None:
        raise ServiceError("unauthorized", "Sign in first.", 401)
    return creds.credentials


SessionTokenDep = Annotated[str, Depends(session_token)]


def current_user(service: ServiceDep, token: SessionTokenDep) -> User:
    """The signed-in account (401 if the session is missing or expired)."""
    return service.session_user(token)


UserDep = Annotated[User, Depends(current_user)]


def optional_user(
    service: ServiceDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_session_bearer)],
) -> User | None:
    """The signed-in account if a session token was sent, else None. A token that was
    sent but has expired is still a 401, so the client notices instead of the player
    silently getting an unlinked seat."""
    return service.session_user(creds.credentials) if creds else None


OptionalUserDep = Annotated[User | None, Depends(optional_user)]
