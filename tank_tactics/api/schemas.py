"""Request and response bodies. Player actions use the game's own ``Action`` union."""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic_core import PydanticCustomError

from ..config import is_valid_unicode


def _unicode_text(value: str) -> str:
    if not is_valid_unicode(value):
        raise PydanticCustomError("invalid_unicode", "must be valid Unicode text")
    return value


# Free text that gets stored and shown to other players.
UnicodeText = Annotated[str, AfterValidator(_unicode_text)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateGameRequest(_Body):
    name: UnicodeText
    # Partial and nested, e.g. {"jury": {"enabled": true}}; validated by GameConfig.
    config: dict[str, Any] | None = None


class JoinRequest(_Body):
    name: UnicodeText


class ConfigUpdateRequest(_Body):
    config: dict[str, Any]


class DiscordConnectRequest(_Body):
    # The site the host is on (``location.origin``); Discord returns them there.
    origin: str = Field(pattern=r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$", max_length=200)


class DiscordConnectStart(BaseModel):
    url: str  # Discord's authorize page, to open in the host's browser


class SchedulePreviewRequest(_Body):
    rrule: UnicodeText
    timezone: UnicodeText


class SchedulePreview(BaseModel):
    rrule: str  # normalized
    timezone: str
    summary: str


class ColorRequest(_Body):
    color: str = Field(description="Hex colour, e.g. #3cb44b.", max_length=16)


class RemovePlayerRequest(_Body):
    player_id: str


class VoteRequest(_Body):
    candidate_id: str


class ChatRequest(_Body):
    to_id: str | None = None  # None = public channel
    text: UnicodeText


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


# -- accounts -------------------------------------------------------------------------

class AuthConfig(BaseModel):
    google_client_id: str | None  # None = Google sign-in is off
    discord_connect: bool = False  # the lobby can offer "Connect to Discord"


class GoogleSignInRequest(_Body):
    credential: str  # the ID token from Google Identity Services


class LinkSeatsRequest(_Body):
    tokens: list[str] = Field(max_length=500)  # seat tokens saved on this device


class SeatsLinked(BaseModel):
    linked: int  # seats newly linked to the account


class UserOut(BaseModel):
    id: str
    name: str


class SignedIn(BaseModel):
    """Returned once on sign-in. The token is the session secret: keep it."""

    token: str
    user: UserOut


class UserGame(BaseModel):
    game_id: str
    player_id: str
    name: str
    status: str
    is_host: bool
