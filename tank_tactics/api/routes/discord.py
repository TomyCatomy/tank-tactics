"""Connecting a lobby to a Discord channel (see ``api/discord_oauth.py`` for the flow)."""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ... import game
from ...game.guards import require_host, require_lobby
from ...service import ServiceError
from ..deps import PlayerDep, ServiceDep
from ..discord_oauth import DiscordConnect, DiscordError
from ..schemas import DiscordConnectRequest, DiscordConnectStart

log = logging.getLogger("tank_tactics")
router = APIRouter(prefix="/api", tags=["lobby"])


def _connector(request: Request) -> DiscordConnect:
    connect = request.app.state.discord
    if connect is None:
        raise ServiceError("not_found", "Discord connect is not enabled on this server.", 404)
    return connect


@router.post("/games/{game_id}/discord/connect")
def start_connect(body: DiscordConnectRequest, request: Request, player: PlayerDep) -> DiscordConnectStart:
    """Host only, in the lobby. Returns the Discord page to send the host's browser to;
    Discord sends it back to ``/api/discord/callback``, which saves the webhook."""
    connect = _connector(request)
    state = player.service.get(player.game_id)
    try:
        require_lobby(state)
        require_host(state, player.player_id)
    except game.GameError as e:
        raise ServiceError(e.code, e.message) from None
    return DiscordConnectStart(url=connect.authorize_url(player.game_id, player.player_id, body.origin))


@router.get("/discord/callback", include_in_schema=False)
def callback(request: Request, service: ServiceDep, state: str = "", code: str = "", error: str = "") -> RedirectResponse:
    """Where Discord sends the host back. Always redirects to the game page, with
    ``?discord=connected|cancelled|failed`` for the client to show (``expired`` if the
    attempt is unknown, so there is no game to go back to)."""
    connect = _connector(request)
    pending = connect.take(state)
    if pending is None:
        return RedirectResponse("/?discord=expired#/", status_code=303)
    outcome = "cancelled" if error or not code else "connected"
    if outcome == "connected":
        try:
            url = connect.exchange(code, pending.redirect_uri)
            patch = {"notifications": {"discord_webhook_url": url}}
            service.update_config(pending.game_id, pending.player_id, patch)
        except (DiscordError, ServiceError) as e:
            log.warning("discord connect failed: %s", e)
            outcome = "failed"
        else:
            connect.greet(url, f"🔗 This channel will get updates from Tank Tactics game `{pending.game_id}`.")
    return RedirectResponse(f"/?discord={outcome}#/g/{quote(pending.game_id)}", status_code=303)
