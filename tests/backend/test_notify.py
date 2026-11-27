"""Discord webhook lines (``api/notify.py``)."""
from __future__ import annotations

import random

from tank_tactics import game
from tank_tactics.api.notify import discord_lines

from ..support import T0, make_active, p

DAY = 86400


def test_ap_tick_is_posted():
    s = make_active({"a": (0, 0), "b": (5, 5)})
    entries = game.advance_time(s, T0 + DAY, random.Random(0))
    assert discord_lines(s, entries) == ["⚡ AP handed out: every tank still in the game got **1 AP**."]


def test_ap_ticks_applied_together_are_one_line():
    s = make_active({"a": (0, 0), "b": (5, 5)}, ap={"cap": 10})
    entries = game.advance_time(s, T0 + 3 * DAY + 10, random.Random(0))
    assert len([e for e in entries if e.event_type == "ap_tick"]) == 3
    assert discord_lines(s, entries) == [
        "⚡ AP handed out: every tank still in the game got **3 AP** (up to the cap)."]


def test_ap_ticks_around_other_events_keep_their_order():
    s = make_active({"a": (0, 0), "b": (1, 1), "c": (9, 9)})
    tick = game.advance_time(s, T0 + DAY, random.Random(0))
    p(s, "b").hp = 1
    kill = game.shoot(s, "a", "b", T0 + DAY)
    assert discord_lines(s, tick + kill) == [
        "⚡ AP handed out: every tank still in the game got **1 AP**.",
        "💥 **A** destroyed **B**.",
    ]


def test_hidden_gifts_are_not_posted():
    s = make_active({"a": (0, 0), "b": (1, 1)}, fog_of_war="alliance")
    p(s, "a").ap = 3
    assert discord_lines(s, game.give_ap(s, "a", "b", 1, T0)) == []
