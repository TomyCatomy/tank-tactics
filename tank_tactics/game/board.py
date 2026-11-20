"""Read-only queries: geometry, tile occupancy and player lookups."""
from __future__ import annotations

from .state import GameError, GameState, Player


def distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Chebyshev distance: diagonal steps count the same as orthogonal ones."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def in_range(a: Player, b: tuple[int, int]) -> bool:
    """No line of sight: range is the only constraint."""
    return distance(a.pos, b) <= a.action_range


def in_bounds(state: GameState, p: tuple[int, int]) -> bool:
    return 0 <= p[0] < state.board_size and 0 <= p[1] < state.board_size


def occupant_at(state: GameState, p: tuple[int, int]) -> Player | None:
    """The living tank (or blocking wreck, if ``wreckage_blocks_tile``) on a tile."""
    for pl in state.players:
        if pl.pos == p and (pl.alive or state.config.board.wreckage_blocks_tile):
            return pl
    return None


def living_players(state: GameState) -> list[Player]:
    return [p for p in state.players if p.alive]


def find_player(state: GameState, player_id: str) -> Player | None:
    return next((p for p in state.players if p.id == player_id), None)


def get_player(state: GameState, player_id: str) -> Player:
    p = find_player(state, player_id)
    if p is None:
        raise GameError("not_found", "No such player in this game.")
    return p


def is_juror(state: GameState, p: Player) -> bool:
    return state.config.jury.enabled and not p.alive and p.joined_jury_at is not None
