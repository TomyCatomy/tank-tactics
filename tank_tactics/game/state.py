"""The game's data model: plain dataclasses, no behaviour beyond serialisation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Literal

from ..config import GameConfig

Visibility = Literal["public", "participants", "actor_only"]


class EventType(StrEnum):
    """What a log entry records. Player actions share their names with ``ActionType``."""

    # lobby
    JOIN = "join"
    LEAVE = "leave"
    LOBBY_CLOSED = "lobby_closed"
    CONFIG = "config"
    GAME_START = "game_start"
    # player actions
    MOVE = "move"
    SHOOT = "shoot"
    GIVE_AP = "give_ap"
    GIVE_HP = "give_hp"
    UPGRADE = "upgrade"
    # consequences
    KILL = "kill"
    LOOT = "loot"
    REMOVED = "removed"
    GAME_OVER = "game_over"
    # time and jury
    AP_TICK = "ap_tick"
    JURY_VOTE = "jury_vote"
    JURY_AWARD = "jury_award"


class GameError(Exception):
    """A rejected action. ``code`` is machine-readable, the message is shown to players."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Player:
    id: str
    name: str
    color: str
    x: int = -1  # -1 before the game starts. Dead tanks keep their last position (for wreckage).
    y: int = -1
    hp: int = 3
    ap: int = 1
    action_range: int = 3  # how far this tank can shoot and give
    alive: bool = True
    is_host: bool = False
    died_at: float | None = None
    joined_jury_at: float | None = None  # set when a player dies while the jury is enabled

    @property
    def pos(self) -> tuple[int, int]:
        return (self.x, self.y)


@dataclass
class LogEntry:
    id: int
    ts: float
    event_type: EventType
    actor_id: str | None = None
    target_id: str | None = None
    amount: int | None = None
    from_xy: tuple[int, int] | None = None
    to_xy: tuple[int, int] | None = None
    details: dict[str, Any] = field(default_factory=dict)
    visibility: Visibility = "public"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        d["from_xy"] = list(self.from_xy) if self.from_xy else None
        d["to_xy"] = list(self.to_xy) if self.to_xy else None
        return d


@dataclass
class JuryVote:
    cycle_index: int
    juror_id: str
    candidate_id: str
    ts: float


@dataclass
class GameState:
    id: str
    config: GameConfig
    created_at: float
    status: Literal["lobby", "active", "finished", "closed"] = "lobby"  # closed = lobby cancelled by host
    started_at: float | None = None
    ended_at: float | None = None
    winner_ids: list[str] = field(default_factory=list)
    end_reason: str | None = None  # last_standing | simultaneous | time_limit
    board_size: int = 10
    last_ap_tick_at: float | None = None
    last_jury_cycle_at: float | None = None
    jury_cycle_index: int = 0
    players: list[Player] = field(default_factory=list)
    log: list[LogEntry] = field(default_factory=list)
    jury_votes: list[JuryVote] = field(default_factory=list)
    next_log_id: int = 1
