"""Tank Tactics configuration.

Every tunable value lives in this file.

* ``GameConfig`` holds the per-game rules, grouped into sections (board, start,
  ap, actions, jury, players, debug, notifications). The host can override any of
  them in the lobby before the game starts.
* ``ServerConfig`` holds process-wide settings, read from environment variables.
"""
from __future__ import annotations

import math
from typing import Any, Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import recurrence

DAY = 86_400
_DISCORD_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}


class ConfigError(ValueError):
    """Invalid game configuration. The message is safe to show to players."""


def is_valid_unicode(text: str) -> bool:
    """False for strings UTF-8 can't encode: JSON escapes can produce lone surrogates."""
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True


class _Section(BaseModel):
    # Strict: no silent coercion ("5" -> 5, true -> 1); extra keys are typos, so reject them.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# -- sections ---------------------------------------------------------------------

class BoardConfig(_Section):
    size: int | None = Field(None, ge=3, le=200, description="Side length. None = auto from player count.")
    min_size: int = Field(12, ge=3, le=200, description="Auto size: minimum side length.")
    size_multiplier: int = Field(3, ge=1, le=20, description="Auto size: ceil(sqrt(players) * this).")
    wreckage_blocks_tile: bool = Field(False, description="Dead tanks leave an impassable wreck.")

    def size_for(self, player_count: int) -> int:
        if self.size is not None:
            return self.size
        return max(self.min_size, math.ceil(math.sqrt(player_count) * self.size_multiplier))


class StartConfig(_Section):
    """Every tank's stats when the game starts."""

    hp: int = Field(3, ge=1, le=100)
    ap: int = Field(1, ge=0, le=1000)
    action_range: int = Field(3, ge=1, le=200)


class ApConfig(_Section):
    """When AP arrives: +1 AP to every living tank on each occurrence of an RFC 5545
    recurrence rule, evaluated in ``timezone`` from the moment the game starts (DTSTART).
    The jury, when enabled, closes a cycle on the same occurrences."""

    rrule: str = Field(
        recurrence.DEFAULT_RULE,
        description="RRULE body, e.g. FREQ=DAILY;BYHOUR=9;BYMINUTE=0. No DTSTART, COUNT or UNTIL. "
        "The default, FREQ=DAILY, is every 24 hours from the start.",
    )
    timezone: str = Field(recurrence.DEFAULT_TIMEZONE, description="IANA time zone the rule is evaluated in.")
    cap: int | None = Field(None, ge=1, le=1_000_000, description="Max AP a tank can hold. None = no cap.")

    @field_validator("rrule")
    @classmethod
    def _rule(cls, v: str) -> str:
        try:
            return recurrence.normalize_rule(v)
        except recurrence.RecurrenceError as e:
            raise ValueError(str(e)) from None

    @field_validator("timezone")
    @classmethod
    def _zone(cls, v: str) -> str:
        try:
            return recurrence.check_timezone(v)
        except recurrence.RecurrenceError as e:
            raise ValueError(str(e)) from None

    @model_validator(mode="after")
    def _fires(self) -> Self:
        # Round minutes, at most a minute apart, every calendar month (see recurrence.validate).
        try:
            recurrence.validate(self.rrule, self.timezone)
        except recurrence.RecurrenceError as e:
            raise ValueError(f"rrule: {e}") from None
        return self


class ActionsConfig(_Section):
    range_upgrade_cost: int = Field(3, ge=1, le=1000, description="AP cost to permanently add 1 range.")
    max_range: int | None = Field(None, ge=1, le=200, description="Range upgrade ceiling. None = unlimited.")
    allow_give_hp: bool = True
    loot_on_kill: bool = Field(True, description="The killer receives all of the victim's AP.")


class JuryConfig(_Section):
    """The optional jury of dead players."""

    enabled: bool = False  # cycles close on every AP occurrence (see ApConfig)
    tie_rule: Literal["all", "random"] = Field("all", description="Ties: all tied get +1 AP, or one at random.")
    votes_public: bool = True


class PlayersConfig(_Section):
    minimum: int = Field(2, ge=2, le=1000, description="A game can never start with fewer than 2.")
    maximum: int = Field(64, ge=2, le=1000)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.maximum < self.minimum:
            raise ValueError("maximum must be >= minimum")
        return self


class DebugConfig(_Section):
    """Testing aid: AP ticks and jury cycles happen every ``tick_seconds`` instead of on
    the ``ap.rrule`` schedule (as if it were FREQ=SECONDLY;INTERVAL=tick_seconds in UTC)."""

    fast_ticks: bool = False
    tick_seconds: int = Field(10, ge=1, le=3600)
    local_seats: bool = Field(
        False,
        description="Let one browser add and switch between several players (for testing). "
        "Client-side convenience only: joining is already open to anyone with the invite link.",
    )


class NotificationsConfig(_Section):
    # Public events only (never fog-protected data); visible to the host only.
    discord_webhook_url: str | None = None

    @field_validator("discord_webhook_url")
    @classmethod
    def _discord_only(cls, v: str | None) -> str | None:
        # Only Discord webhooks, so the server can't be made to call arbitrary URLs.
        if v is None:
            return v
        u = urlparse(v)
        if u.scheme != "https" or u.hostname not in _DISCORD_HOSTS or not u.path.startswith("/api/webhooks/"):
            raise ValueError("must be a Discord webhook URL (https://discord.com/api/webhooks/...)")
        return v


# -- the whole game config ------------------------------------------------------------

class GameConfig(_Section):
    """Per-game rules. Immutable: use ``with_overrides`` to derive a changed copy."""

    board: BoardConfig = BoardConfig()
    start: StartConfig = StartConfig()
    ap: ApConfig = ApConfig()
    actions: ActionsConfig = ActionsConfig()
    jury: JuryConfig = JuryConfig()
    fog_of_war: Literal["off", "alliance"] = Field("off", description="'alliance' hides AP counts, gifts and loot.")
    max_duration_seconds: int | None = Field(None, ge=1, le=10 * 365 * DAY, description="Time limit. None = none.")
    players: PlayersConfig = PlayersConfig()
    debug: DebugConfig = DebugConfig()
    notifications: NotificationsConfig = NotificationsConfig()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.actions.max_range is not None and self.actions.max_range < self.start.action_range:
            raise ValueError("actions.max_range must be >= start.action_range")
        if self.ap.cap is not None and self.start.ap > self.ap.cap:
            raise ValueError("start.ap must be <= ap.cap")
        return self

    # -- construction -------------------------------------------------------------

    @classmethod
    def parse(cls, data: Any) -> GameConfig:
        """Validate untrusted input, raising ConfigError with a readable message."""
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ConfigError("config must be an object")
        try:
            cfg = cls.model_validate(data)
        except ValidationError as e:
            raise ConfigError(_describe(e)) from None
        # Checked here, not in a field validator, so from_stored can still load old games.
        if not is_valid_unicode(cfg.notifications.discord_webhook_url or ""):
            raise ConfigError("notifications.discord_webhook_url: must be valid Unicode text")
        return cfg

    def with_overrides(self, patch: Any) -> GameConfig:
        """A validated copy with ``patch`` applied. The patch is untrusted and partial:
        ``{"jury": {"enabled": True}}`` changes one field and keeps the rest."""
        if patch is None:
            return self
        if not isinstance(patch, dict):
            raise ConfigError("config must be an object")
        return self.parse(_deep_merge(self.model_dump(), patch))

    @classmethod
    def from_stored(cls, data: dict[str, Any]) -> GameConfig:
        """Load a saved config; fields added since get defaults, removed fields are dropped."""
        return cls.model_validate(_known_fields(cls, data))

    # -- derived values -----------------------------------------------------------

    @property
    def effective_schedule(self) -> tuple[str, str]:
        """(rule, time zone) that AP ticks and jury closes actually follow. With
        debug.fast_ticks it is a plain ``tick_seconds`` interval in UTC instead."""
        if self.debug.fast_ticks:
            return recurrence.debug_rule(self.debug.tick_seconds), recurrence.DEFAULT_TIMEZONE
        return self.ap.rrule, self.ap.timezone


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _known_fields(model: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if name not in data:
            continue
        sub = field.annotation
        if isinstance(sub, type) and issubclass(sub, BaseModel) and isinstance(data[name], dict):
            out[name] = _known_fields(sub, data[name])
        else:
            out[name] = data[name]
    return out


def _describe(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        field = ".".join(str(x) for x in err["loc"])
        msg = err["msg"].removeprefix("Value error, ")
        if field == "ap" and msg.startswith("rrule: "):  # from ApConfig's whole-model check
            field, msg = "ap.rrule", msg.removeprefix("rrule: ")
        parts.append(f"{field}: {msg}" if field else msg)
    return "; ".join(parts)


# -- server -------------------------------------------------------------------------

class ServerConfig(BaseSettings):
    """Process-wide settings. Each field reads the environment variable of the same
    name, case-insensitively (``PORT``, ``DB_PATH``, ...); keyword arguments win."""

    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    host: str = "0.0.0.0"
    port: int = Field(8080, ge=1, le=65535)
    db_path: str = "data/tank-tactics.db"
    allow_debug: bool = Field(
        False, description="Let hosts turn on the debug.* game options. Off hides them and rejects them in the API."
    )
    action_rate_per_second: float = Field(5.0, gt=0, description="Max game actions per player per second.")
    chat_rate_per_10s: int = Field(20, ge=1, description="Max chat messages per player per 10 seconds.")
    scheduler_max_sleep: float = Field(
        5.0, gt=0, description="Upper bound on scheduler sleeps; it otherwise wakes at the next due event."
    )
    max_message_length: int = Field(1000, ge=1, le=100_000)
    archive_cache_size: int = Field(
        16, ge=0, description="Finished/closed games kept in memory after use; the rest are read from disk."
    )
    google_client_id: str | None = Field(
        None, description="OAuth client ID for 'Sign in with Google'. Unset = Google sign-in is off."
    )
    session_ttl_days: int = Field(30, ge=1, le=365, description="How long a sign-in lasts before it must be renewed.")
    login_rate_per_minute: int = Field(10, ge=1, description="Max sign-in attempts per client IP per minute.")
