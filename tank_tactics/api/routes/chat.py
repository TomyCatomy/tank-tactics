"""Public chat and private messages."""
from __future__ import annotations

from fastapi import APIRouter

from ..deps import PlayerDep
from ..schemas import ChatRequest, MessageOut

router = APIRouter(prefix="/api/games/{game_id}", tags=["chat"])


@router.post("/chat", status_code=201)
def send_message(body: ChatRequest, player: PlayerDep) -> MessageOut:
    """``to_id: null`` posts to the public channel; a player id sends a private message."""
    msg = player.service.send_message(player.game_id, player.player_id, body.to_id, body.text)
    return MessageOut(**msg.to_dict())
