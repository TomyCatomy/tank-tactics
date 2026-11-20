"""Request and response bodies. Player actions use the game's own ``Action`` union."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateGameRequest(_Body):
    name: str
    # Partial and nested, e.g. {"jury": {"enabled": true}}; validated by GameConfig.
    config: dict[str, Any] | None = None


class JoinRequest(_Body):
    name: str


class ConfigUpdateRequest(_Body):
    config: dict[str, Any]


class RemovePlayerRequest(_Body):
    player_id: str


class VoteRequest(_Body):
    candidate_id: str


class ChatRequest(_Body):
    to_id: str | None = None  # None = public channel
    text: str


class Credentials(BaseModel):
    """Returned once on create/join. The token is the player's secret: keep it."""

    game_id: str
    player_id: str
    token: str


class GameInfo(BaseModel):
    """What someone holding only the invite link may see."""

    id: str
    status: str
    host: str | None
    player_count: int
    max_players: int


class MessageOut(BaseModel):
    id: int
    ts: float
    from_id: str
    to_id: str | None
    text: str


class Ok(BaseModel):
    ok: bool = True
