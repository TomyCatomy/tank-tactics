"""Plain helpers shared by the backend and frontend test suites.

Fixtures live in each suite's ``conftest.py``; these are ordinary functions that
tests import directly.
"""
from __future__ import annotations

import random
from typing import Any

from tank_tactics import game
from tank_tactics.api.google import GoogleIdentity, InvalidGoogleToken
from tank_tactics.config import GameConfig, ServerConfig
from tank_tactics.service import GameService
from tank_tactics.store import Store

T0 = 1_000_000.0
DAY = 86_400


# -- pure rules engine ----------------------------------------------------------------

def make_active(
    positions: dict[str, tuple[int, int]],
    board_size: int = 10,
    now: float = T0,
    **config: Any,
) -> game.GameState:
    """An active game with tanks at fixed positions. The first id is the host.

    Config sections are keyword arguments: ``make_active(pos, jury={"enabled": True})``.
    """
    cfg = GameConfig.parse(config)
    ids = list(positions)
    state = game.create_game("game1", ids[0], ids[0].upper(), cfg, now)
    for pid in ids[1:]:
        game.add_player(state, pid, pid.upper(), now)
    game.start_game(state, ids[0], now, random.Random(1))
    state.board_size = board_size
    for pl in state.players:
        pl.x, pl.y = positions[pl.id]
    return state


def p(state: game.GameState, pid: str) -> game.Player:
    return game.get_player(state, pid)


# -- service ------------------------------------------------------------------------------

class Clock:
    """A settable clock to inject into GameService (advance with ``clock.t += ...``)."""

    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_service(path: str = ":memory:", clock: Clock | None = None, **server_cfg: Any) -> tuple[GameService, Clock]:
    """A GameService on SQLite (in memory by default) with a fake clock and seeded RNG."""
    clock = clock or Clock()
    server_cfg.setdefault("allow_debug", True)  # tests use debug options; pass False to test the lock
    svc = GameService(Store(path), ServerConfig(**server_cfg), clock=clock, rng=random.Random(42))
    return svc, clock


def setup_game(svc: GameService, names=("Ann", "Bob", "Cat"), config=None):
    """Create, join and start a game. Returns (game_id, player_ids, credentials)."""
    host = svc.create_game(names[0], config)
    gid = host["game_id"]
    players = [host] + [svc.join_game(gid, n) for n in names[1:]]
    svc.start_game(gid, host["player_id"])
    return gid, [pl["player_id"] for pl in players], players


def place(svc: GameService, gid: str, positions: dict[str, tuple[int, int]]) -> None:
    """Put tanks at fixed tiles (and persist), bypassing the random start placement."""
    s = svc.games[gid]
    for pid, (x, y) in positions.items():
        pl = game.get_player(s, pid)
        pl.x, pl.y = x, y
    svc.store.save(s)


# -- Google sign-in ------------------------------------------------------------------------

FAKE_GOOGLE_CLIENT_ID = "test-client.apps.googleusercontent.com"


def fake_google_verifier(credential: str) -> GoogleIdentity:
    """Stands in for real Google ID-token checks: accepts ``good:<sub>:<name>``."""
    parts = credential.split(":")
    if len(parts) != 3 or parts[0] != "good":
        raise InvalidGoogleToken("bad")
    return GoogleIdentity(sub=parts[1], name=parts[2])
