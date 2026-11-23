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
from .hub import Hub
from .google import GoogleTokenVerifier, GoogleVerifier
from .routes import auth, chat, games, live, lobby, play

PUBLIC_DIR = Path(__file__).resolve().parent.parent.parent / "public"
MAX_BODY_BYTES = 64 * 1024


def create_app(service: GameService, google_verifier: GoogleVerifier | None = None) -> FastAPI:
    """``google_verifier`` checks Google ID tokens; by default it is built from
    ``GOOGLE_CLIENT_ID``, and Google sign-in is off when that is unset."""
    hub = Hub(service)
    if google_verifier is None and service.cfg.google_client_id:
        google_verifier = GoogleTokenVerifier(service.cfg.google_client_id)

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
    install_error_handlers(app)

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        size = request.headers.get("content-length")
        if size is not None and size.isdigit() and int(size) > MAX_BODY_BYTES:
            return error_response("too_large", "Request body too large.", 413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    for module in (games, lobby, play, chat, live, auth):
        app.include_router(module.router)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(PUBLIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")
    return app
