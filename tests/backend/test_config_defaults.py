"""The settings a new game starts with."""
from __future__ import annotations

from tank_tactics.config import GameConfig


def test_new_game_defaults():
    c = GameConfig()
    assert c.start.ap == 0
    assert c.actions.loot_on_kill is False
    assert c.jury.enabled is True
    assert c.max_duration_seconds is None
