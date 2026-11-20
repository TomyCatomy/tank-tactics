"""Pure Tank Tactics game logic (section 2 of the spec).

No I/O, no clocks, no global randomness: callers pass ``now`` (seconds since the
epoch, float) and an ``rng`` (``random.Random``) where needed.

Every mutating function validates everything first and only then mutates, so a
raised ``GameError`` never leaves the state half-changed. Each returns the log
entries it appended, so the persistence layer can store just those.

Modules:
    state    data model (GameState, Player, LogEntry, JuryVote, GameError)
    board    geometry, occupancy and player lookups
    guards   shared validation checks
    events   log entries and AP gains
    outcome  death and winning
    lobby    setup: create, join, configure, remove, start
    actions  move, shoot, give AP / HP, upgrade range
    commands typed action requests (ActionType enum + one model per action)
    jury     the optional jury of dead players
    clock    AP ticks, jury cycles, time limit, restart catch-up
    view     per-viewer fog-of-war redaction
"""
from .actions import MOVE_COST, SHOOT_COST, give_ap, give_hp, move, shoot, upgrade_range
from .board import distance, find_player, get_player, in_bounds, in_range, is_juror, living_players, occupant_at
from .commands import (
    Action,
    ActionType,
    GiveApCommand,
    GiveHpCommand,
    MoveCommand,
    ShootCommand,
    UpgradeCommand,
    parse_action,
)
from .clock import advance_time, game_ends_at, next_ap_tick_at, next_due_at, next_jury_close_at
from .jury import cast_jury_vote
from .lobby import (
    PLAYER_COLORS,
    add_player,
    close_lobby,
    create_game,
    leave_lobby,
    place_tanks,
    remove_player,
    start_game,
    update_config,
)
from .outcome import check_game_over
from .state import EventType, GameError, GameState, JuryVote, LogEntry, Player, Visibility
from .view import can_see, view_for

__all__ = [
    # state
    "EventType", "GameError", "GameState", "JuryVote", "LogEntry", "Player", "Visibility",
    # board
    "distance", "find_player", "get_player", "in_bounds", "in_range", "is_juror", "living_players", "occupant_at",
    # lobby
    "PLAYER_COLORS", "add_player", "close_lobby", "create_game", "leave_lobby", "place_tanks", "remove_player",
    "start_game", "update_config",
    # actions
    "MOVE_COST", "SHOOT_COST", "give_ap", "give_hp", "move", "shoot", "upgrade_range",
    # commands
    "Action", "ActionType", "GiveApCommand", "GiveHpCommand", "MoveCommand", "ShootCommand", "UpgradeCommand",
    "parse_action",
    # jury
    "cast_jury_vote",
    # outcome
    "check_game_over",
    # clock
    "advance_time", "game_ends_at", "next_ap_tick_at", "next_due_at", "next_jury_close_at",
    # view
    "can_see", "view_for",
]
