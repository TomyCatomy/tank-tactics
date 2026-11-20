"""Player action commands: what a client may ask a living tank to do.

Each command is a small validated model that knows how to apply itself. The
``type`` field is the discriminator, so ``parse_action`` (and FastAPI, which uses
the same ``Action`` union) picks the right model from the payload. The models only
check shapes and types; game rules (range, AP, bounds) are enforced by ``actions``.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, ValidationError

from .actions import give_ap, give_hp, move, shoot, upgrade_range
from .state import GameError, GameState, LogEntry


class ActionType(StrEnum):
    MOVE = "move"
    SHOOT = "shoot"
    GIVE_AP = "give_ap"
    GIVE_HP = "give_hp"
    UPGRADE = "upgrade"


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def apply(self, state: GameState, actor_id: str, now: float) -> list[LogEntry]:
        raise NotImplementedError


class MoveCommand(_Command):
    type: Literal[ActionType.MOVE]
    to: tuple[StrictInt, StrictInt] = Field(description="Destination tile [x, y]; must be adjacent.")

    def apply(self, state: GameState, actor_id: str, now: float) -> list[LogEntry]:
        return move(state, actor_id, self.to, now)


class ShootCommand(_Command):
    type: Literal[ActionType.SHOOT]
    target_id: str

    def apply(self, state: GameState, actor_id: str, now: float) -> list[LogEntry]:
        return shoot(state, actor_id, self.target_id, now)


class GiveApCommand(_Command):
    type: Literal[ActionType.GIVE_AP]
    target_id: str
    amount: StrictInt

    def apply(self, state: GameState, actor_id: str, now: float) -> list[LogEntry]:
        return give_ap(state, actor_id, self.target_id, self.amount, now)


class GiveHpCommand(_Command):
    type: Literal[ActionType.GIVE_HP]
    target_id: str
    amount: StrictInt

    def apply(self, state: GameState, actor_id: str, now: float) -> list[LogEntry]:
        return give_hp(state, actor_id, self.target_id, self.amount, now)


class UpgradeCommand(_Command):
    type: Literal[ActionType.UPGRADE]

    def apply(self, state: GameState, actor_id: str, now: float) -> list[LogEntry]:
        return upgrade_range(state, actor_id, now)


Action = Annotated[
    MoveCommand | ShootCommand | GiveApCommand | GiveHpCommand | UpgradeCommand,
    Field(discriminator="type"),
]
_ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


def parse_action(raw: Any) -> Action:
    """Validate an untrusted action payload. Raises GameError('invalid_action')."""
    if isinstance(raw, _Command):
        return raw  # type: ignore[return-value]
    try:
        return _ACTION_ADAPTER.validate_python(raw)
    except ValidationError as e:
        valid = ", ".join(t.value for t in ActionType)
        problems = []
        for err in e.errors():
            if err["type"] in ("union_tag_invalid", "union_tag_not_found", "model_attributes_type"):
                problems.append(f"'type' must be one of: {valid}")
            else:
                problems.append(f"{'.'.join(str(x) for x in err['loc'][1:])}: {err['msg']}")
        raise GameError("invalid_action", "Invalid action: " + "; ".join(problems) + ".") from None
