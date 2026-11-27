"""The FastAPI app: REST endpoints, the live WebSocket feed, and the web client.

Routes live in ``api/routes/``, one module per area. Endpoints are plain ``def``
functions, so FastAPI runs them in its thread pool; the GameService is thread-safe
(per-game mutex) and notifies the Hub, which pushes updates over WebSockets.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..service import GameService
from .background import run_scheduler
from .errors import error_response, install_error_handlers
from .discord_oauth import DiscordConnect
from .hub import Hub
from .google import GoogleTokenVerifier, GoogleVerifier
from .routes import auth, chat, discord, games, live, lobby, play

PUBLIC_DIR = Path(__file__).resolve().parent.parent.parent / "public"
MAX_BODY_BYTES = 64 * 1024


def create_app(
    service: GameService, google_verifier: GoogleVerifier | None = None, discord_connect: DiscordConnect | None = None
) -> FastAPI:
    """``google_verifier`` checks Google ID tokens; by default it is built from
    ``GOOGLE_CLIENT_ID``, and Google sign-in is off when that is unset. ``discord_connect``
    likewise defaults to ``DISCORD_CLIENT_ID`` + ``DISCORD_CLIENT_SECRET`` (off without both)."""
    hub = Hub(service)
    cfg = service.cfg
    if google_verifier is None and cfg.google_client_id:
        google_verifier = GoogleTokenVerifier(cfg.google_client_id)
    if discord_connect is None and cfg.discord_client_id and cfg.discord_client_secret:
        discord_connect = DiscordConnect(cfg.discord_client_id, cfg.discord_client_secret.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub.attach(asyncio.get_running_loop())
        hub.spawn(run_scheduler(hub))  # its first pass catches up anything missed while down
        yield
        await hub.close()

    app = FastAPI(title="Tank Tactics", version="1.0.0", lifespan=lifespan)
    app.state.service = service
    app.state.hub = hub
    app.state.google_verifier = google_verifier
    app.state.discord = discord_connect
    install_error_handlers(app)

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        size = request.headers.get("content-length")
        if size is not None and size.isdigit() and int(size) > MAX_BODY_BYTES:
            return error_response("too_large", "Request body too large.", 413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Google's sign-in button checks the page origin via the referrer, so send the
        # origin (never the path) cross-origin. Rejoin tokens live in the URL fragment,
        # which browsers never put in a referrer.
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    for module in (games, lobby, play, chat, live, auth, discord):
        app.include_router(module.router)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(PUBLIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")
    return app
