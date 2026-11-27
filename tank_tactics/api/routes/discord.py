"""Connecting a lobby to a Discord channel (see ``api/discord_oauth.py`` for the flow)."""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from ... import game
from ...game.guards import require_host, require_lobby
from ...service import ServiceError
from ..deps import PlayerDep, ServiceDep
from ..discord_oauth import CALLBACK_PATH, STATE_TTL, DiscordConnect, DiscordError
from ..schemas import DiscordConnectRequest, DiscordConnectStart

log = logging.getLogger("tank_tactics")
router = APIRouter(prefix="/api", tags=["lobby"])

# Holds the browser key of the host's latest connect attempt (see api/discord_oauth.py).
# Only sent back to the callback. SameSite=Lax still sends it when Discord redirects the
# browser there, since that is a top-level GET navigation.
COOKIE = "tt_discord_connect"


def _connector(request: Request) -> DiscordConnect:
    connect = request.app.state.discord
    if connect is None:
        raise ServiceError("not_found", "Discord connect is not enabled on this server.", 404)
    return connect


@router.post("/games/{game_id}/discord/connect")
def start_connect(body: DiscordConnectRequest, request: Request, response: Response,
                  player: PlayerDep) -> DiscordConnectStart:
    """Host only, in the lobby. Returns the Discord page to send the host's browser to;
    Discord sends it back to ``/api/discord/callback``, which saves the webhook."""
    connect = _connector(request)
    state = player.service.get(player.game_id)
    try:
        require_lobby(state)
        require_host(state, player.player_id)
    except game.GameError as e:
        raise ServiceError(e.code, e.message) from None
    started = connect.start(player.game_id, player.player_id, body.origin)
    response.set_cookie(COOKIE, started.browser_key, max_age=int(STATE_TTL), path=CALLBACK_PATH,
                        secure=request.url.scheme == "https", httponly=True, samesite="lax")
    return DiscordConnectStart(url=started.url)


@router.get("/discord/callback", include_in_schema=False)
def callback(request: Request, service: ServiceDep, state: str = "", code: str = "", error: str = "") -> RedirectResponse:
    """Where Discord sends the host back. Always redirects to the game page, with
    ``?discord=connected|cancelled|failed`` for the client to show (``expired`` if the
    attempt is unknown or this browser didn't start it, so there is no game to go back to)."""
    connect = _connector(request)
    pending = connect.take(state, request.cookies.get(COOKIE, ""))
    if pending is None:
        return _done("/?discord=expired#/")
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
    return _done(f"/?discord={outcome}#/g/{quote(pending.game_id)}")


def _done(location: str) -> RedirectResponse:
    r = RedirectResponse(location, status_code=303)
    r.delete_cookie(COOKIE, path=CALLBACK_PATH)
    return r
