"""The live WebSocket feed."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...service import ServiceError
from ..hub import Hub

router = APIRouter(tags=["live"])


@router.websocket("/ws/{game_id}")
async def live(ws: WebSocket, game_id: str) -> None:
    """Send ``{"type": "auth", "token": ...}`` first (so the token stays out of URLs and
    access logs). The server then pushes ``{"type": "state", "state": ...}`` with the
    player's redacted view on every change."""
    hub: Hub = ws.app.state.hub
    await ws.accept()
    try:
        data = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        token = data.get("token") if isinstance(data, dict) else None
        player_id = hub.service.authenticate(game_id, token)
    except WebSocketDisconnect:
        return
    except (ServiceError, TimeoutError, json.JSONDecodeError) as e:
        msg = e.message if isinstance(e, ServiceError) else "Authentication failed."
        await ws.send_json({"type": "error", "error": {"code": "unauthorized", "message": msg}})
        await ws.close(code=4401)
        return

    hub.connect(game_id, ws, player_id)
    try:
        await hub.send_state(game_id, ws, player_id)
        while True:
            if await ws.receive_text() == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(game_id, ws)
