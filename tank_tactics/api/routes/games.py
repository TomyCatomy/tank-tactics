"""Creating, finding and joining games, and reading a player's view of one."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ... import recurrence
from ...config import GameConfig
from ...service import ServiceError
from ..deps import OptionalUserDep, PlayerDep, ServiceDep
from ..schemas import CreateGameRequest, Credentials, GameInfo, JoinRequest, SchedulePreview, SchedulePreviewRequest

router = APIRouter(prefix="/api", tags=["games"])


@router.get("/defaults")
def defaults(service: ServiceDep) -> dict[str, Any]:
    """The default game rules (what the create form starts from), and the time zones
    the AP schedule accepts."""
    return {"config": GameConfig().model_dump(), "allow_debug": service.cfg.allow_debug,
            "timezones": recurrence.timezones()}


@router.post("/schedule/preview")
def schedule_preview(body: SchedulePreviewRequest, service: ServiceDep, request: Request) -> SchedulePreview:
    """Check an AP schedule (``ap.rrule`` + ``ap.timezone``) the way the lobby will, and
    return the normalized rule and a human-readable summary.
    An invalid rule is a 400 with code ``invalid_schedule`` and a message to show inline."""
    client = request.client.host if request.client else "unknown"
    if not service.preview_limiter.allow(client):
        raise ServiceError("rate_limited", "Slow down: too many schedule previews.", 429)
    return SchedulePreview(**service.preview_schedule(body.rrule, body.timezone))


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


@router.post("/games/{game_id}/reset-link")
def reset_link(player: PlayerDep) -> Credentials:
    """Replace the caller's seat token, so every older copy of it (rejoin links, other
    devices) stops working, and their live connections are closed. The seat is unlinked
    from any account; post the new token to ``/api/me/seats/link`` to link it again."""
    return Credentials(**player.service.reset_seat_token(player.game_id, player.player_id))
