"""Live updates: tracks WebSocket connections and pushes per-viewer state on every change."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

from fastapi import WebSocket

from .. import game
from ..service import GameService
from .notify import discord_line, post_discord

log = logging.getLogger("tank_tactics")


class Hub:
    def __init__(self, service: GameService):
        self.service = service
        self.sockets: dict[str, dict[WebSocket, str]] = {}  # game id -> {socket: player id}
        self.wake = asyncio.Event()  # set on every change so the scheduler recomputes its sleep
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start receiving change notifications from the service."""
        self._loop = loop
        self.service.on_change = self._on_change

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- connections ------------------------------------------------------------------

    def connect(self, game_id: str, ws: WebSocket, player_id: str) -> None:
        self.sockets.setdefault(game_id, {})[ws] = player_id

    def disconnect(self, game_id: str, ws: WebSocket) -> None:
        self.sockets.get(game_id, {}).pop(ws, None)

    async def send_state(self, game_id: str, ws: WebSocket, player_id: str) -> None:
        await ws.send_json({"type": "state", "state": self.service.view(game_id, player_id)})

    async def broadcast(self, game_id: str) -> None:
        state = self.service.get(game_id)
        for ws, pid in list(self.sockets.get(game_id, {}).items()):
            try:
                if state is None or game.find_player(state, pid) is None:
                    # Left or was removed from the lobby: tell them, and stop pushing.
                    await ws.send_json({"type": "error", "error": {
                        "code": "not_in_game", "message": "You are no longer in this game."}})
                    self.disconnect(game_id, ws)
                    await ws.close(code=4403)
                    continue
                await self.send_state(game_id, ws, pid)
            except Exception:
                self.disconnect(game_id, ws)

    # -- change notifications ---------------------------------------------------------

    def _on_change(self, game_id: str, entries: list[game.LogEntry]) -> None:
        # Called from whichever thread ran the request; hop onto the event loop.
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._changed, game_id, entries)

    def _changed(self, game_id: str, entries: list[game.LogEntry]) -> None:
        self.wake.set()  # e.g. a game just started: the next due tick moved earlier
        self.spawn(self.broadcast(game_id))
        state = self.service.get(game_id)
        url = state.config.notifications.discord_webhook_url if state else None
        if state is not None and url and entries:
            lines = [line for line in (discord_line(state, e) for e in entries) if line]
            if lines:
                self.spawn(post_discord(url, lines))

    async def close(self) -> None:
        for sockets in self.sockets.values():
            for ws in list(sockets):
                try:
                    await ws.close()
                except Exception:
                    pass
        for task in list(self._tasks):
            task.cancel()
