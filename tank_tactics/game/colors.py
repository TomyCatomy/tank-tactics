"""Tank colours: the palette new players are given from, and choosing your own in the lobby."""
from __future__ import annotations

import re
from typing import Any

from .board import get_player
from .guards import require_lobby
from .state import GameError, GameState, LogEntry

PLAYER_COLORS = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6",
    "#bfef45", "#fabed4", "#469990", "#dcbeff", "#9a6324", "#fffac8", "#800000", "#aaffc3",
    "#808000", "#ffd8b1", "#5e60ce", "#a9a9a9", "#ff6f61", "#6b5b95", "#88b04b", "#f7cac9",
]

_HEX = re.compile(r"#[0-9a-f]{6}")
# Chosen colours must be at least this far (redmean distance, 0-765) from everyone else's.
MIN_COLOR_DISTANCE = 60
# ...and at least this bright (relative luminance, 0-1), or the tank disappears into the dark board.
MIN_LUMINANCE = 0.04


def next_free_color(state: GameState) -> str:
    """The first palette colour nobody has yet, or a generated one once the palette runs out."""
    used = {p.color for p in state.players}
    color = next((c for c in PLAYER_COLORS if c not in used), None)
    return color or f"hsl({(len(state.players) * 137.508) % 360:.0f} 70% 55%)"


def _rgb(color: str) -> tuple[int, int, int] | None:
    if not _HEX.fullmatch(color):
        return None  # a generated hsl() colour
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """"Redmean" distance: plain RGB distance weighted the way the eye sees it."""
    r = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return ((2 + r / 256) * dr * dr + 4 * dg * dg + (2 + (255 - r) / 256) * db * db) ** 0.5


def _luminance(rgb: tuple[int, int, int]) -> float:
    lin = [(v / 255 / 12.92) if v <= 10 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def set_color(state: GameState, player_id: str, raw: Any, now: float) -> list[LogEntry]:
    """A player picks their tank's colour, before the game starts. Nothing is logged."""
    require_lobby(state)
    player = get_player(state, player_id)
    color = raw.lower() if isinstance(raw, str) else ""
    rgb = _rgb(color)
    if rgb is None:
        raise GameError("invalid_color", "A colour is a hex code like #3cb44b.")
    if _luminance(rgb) < MIN_LUMINANCE:
        raise GameError("invalid_color", "That colour is too dark to see on the board.")
    for other in state.players:
        theirs = _rgb(other.color)
        if other.id != player.id and theirs is not None and color_distance(rgb, theirs) < MIN_COLOR_DISTANCE:
            raise GameError("color_taken", f"That colour is too close to {other.name}'s.")
    player.color = color
    return []
