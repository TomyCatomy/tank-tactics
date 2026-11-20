"""Tests for the pure game logic (spec section 7)."""
from __future__ import annotations

import json
import random
from contextlib import contextmanager

import pytest

from tank_tactics import game
from tank_tactics.config import ConfigError, GameConfig

from ..support import T0, make_active, p

DAY = 86_400


@contextmanager
def err(code: str):
    with pytest.raises(game.GameError) as info:
        yield info
    assert info.value.code == code, info.value.message


# 1. Chebyshev range ---------------------------------------------------------

def test_chebyshev_distance():
    assert game.distance((0, 0), (1, 1)) == 1
    assert game.distance((0, 0), (2, 1)) == 2
    assert game.distance((5, 5), (2, 7)) == 3


def test_diagonal_in_range_and_distance_3_out_of_range():
    s = make_active({"a": (0, 0), "b": (1, 1), "c": (3, 0)})
    a = p(s, "a")
    assert a.action_range == 2
    assert game.in_range(a, (1, 1))
    assert game.in_range(a, (2, 2))
    assert not game.in_range(a, (3, 0))
    assert not game.in_range(a, (3, 3))


# 2. Move --------------------------------------------------------------------

def test_move_success_costs_one_ap():
    s = make_active({"a": (0, 0), "b": (5, 5)})
    entries = game.move(s, "a", [1, 1], T0)
    assert p(s, "a").pos == (1, 1)
    assert p(s, "a").ap == 0
    assert entries[0].event_type == "move" and entries[0].from_xy == (0, 0) and entries[0].to_xy == (1, 1)


def test_move_rejected_if_occupied():
    s = make_active({"a": (0, 0), "b": (1, 0)})
    with err("occupied"):
        game.move(s, "a", [1, 0], T0)
    assert p(s, "a").pos == (0, 0) and p(s, "a").ap == 1


def test_move_rejected_off_board():
    s = make_active({"a": (0, 0), "b": (5, 5)})
    with err("out_of_bounds"):
        game.move(s, "a", [-1, 0], T0)
    s2 = make_active({"a": (9, 9), "b": (5, 5)})
    with err("out_of_bounds"):
        game.move(s2, "a", [10, 9], T0)


def test_move_rejected_non_adjacent_or_same_tile():
    s = make_active({"a": (0, 0), "b": (5, 5)})
    with err("not_adjacent"):
        game.move(s, "a", [2, 0], T0)
    with err("not_adjacent"):
        game.move(s, "a", [0, 0], T0)


def test_move_rejected_with_zero_ap():
    s = make_active({"a": (0, 0), "b": (5, 5)})
    p(s, "a").ap = 0
    with err("insufficient_ap"):
        game.move(s, "a", [1, 0], T0)


def test_move_rejects_garbage_destination():
    s = make_active({"a": (0, 0), "b": (5, 5)})
    for bad in (None, "x", [1], [1.5, 0], [True, 0]):
        with err("out_of_bounds"):
            game.move(s, "a", bad, T0)


def test_wreckage_blocks_tile_flag():
    s = make_active({"a": (0, 0), "b": (1, 0), "c": (5, 5)}, board={"wreckage_blocks_tile": True})
    p(s, "b").hp = 1
    game.shoot(s, "a", "b", T0)
    p(s, "a").ap = 5
    with err("occupied"):
        game.move(s, "a", [1, 0], T0)

    s2 = make_active({"a": (0, 0), "b": (1, 0), "c": (5, 5)})
    p(s2, "b").hp = 1
    game.shoot(s2, "a", "b", T0)
    p(s2, "a").ap = 5
    game.move(s2, "a", [1, 0], T0)  # default: dead tanks don't block


# 3. Shoot -------------------------------------------------------------------

def test_shoot_costs_one_ap_and_deals_one_damage():
    s = make_active({"a": (0, 0), "b": (2, 2)})
    game.shoot(s, "a", "b", T0)
    assert p(s, "a").ap == 0
    assert p(s, "b").hp == 2


def test_shoot_rejected_out_of_range():
    s = make_active({"a": (0, 0), "b": (3, 0)})
    with err("out_of_range"):
        game.shoot(s, "a", "b", T0)
    assert p(s, "a").ap == 1 and p(s, "b").hp == 3


def test_shoot_rejected_on_self():
    s = make_active({"a": (0, 0), "b": (3, 0)})
    with err("invalid_target"):
        game.shoot(s, "a", "a", T0)


def test_shoot_rejected_on_dead_target():
    s = make_active({"a": (0, 0), "b": (1, 0), "c": (2, 0)})
    p(s, "b").hp = 1
    game.shoot(s, "a", "b", T0)
    p(s, "c").ap = 1
    with err("invalid_target"):
        game.shoot(s, "c", "b", T0)


def test_shoot_rejected_without_ap():
    s = make_active({"a": (0, 0), "b": (1, 0)})
    p(s, "a").ap = 0
    with err("insufficient_ap"):
        game.shoot(s, "a", "b", T0)


def test_can_shoot_through_other_tanks():
    s = make_active({"a": (0, 0), "b": (1, 0), "c": (2, 0)})
    game.shoot(s, "a", "c", T0)
    assert p(s, "c").hp == 2


# 4. Kill, loot, spectators/jurors, win ---------------------------------------

def test_kill_transfers_loot_and_victim_becomes_spectator():
    s = make_active({"a": (0, 0), "b": (1, 0), "c": (5, 5)})
    p(s, "b").hp = 1
    p(s, "b").ap = 7
    entries = game.shoot(s, "a", "b", T0)
    b = p(s, "b")
    assert not b.alive and b.hp == 0 and b.ap == 0
    assert p(s, "a").ap == 7  # 1 - 1 + 7
    assert [e.event_type for e in entries] == ["shoot", "kill", "loot"]
    assert not game.is_juror(s, b)  # jury disabled by default: spectator
    assert s.status == "active"
    # Dead players can't act.
    with err("dead"):
        game.move(s, "b", [2, 0], T0)
    with err("dead"):
        game.shoot(s, "b", "a", T0)


def test_loot_disabled():
    s = make_active({"a": (0, 0), "b": (1, 0), "c": (5, 5)}, actions={"loot_on_kill": False})
    p(s, "b").hp = 1
    p(s, "b").ap = 7
    entries = game.shoot(s, "a", "b", T0)
    assert p(s, "a").ap == 0
    assert "loot" not in [e.event_type for e in entries]


def test_victim_becomes_juror_when_enabled():
    s = make_active({"a": (0, 0), "b": (1, 0), "c": (5, 5)}, jury={"enabled": True})
    p(s, "b").hp = 1
    game.shoot(s, "a", "b", T0)
    assert game.is_juror(s, p(s, "b"))


def test_win_at_one_survivor():
    s = make_active({"a": (0, 0), "b": (1, 0)})
    p(s, "b").hp = 1
    entries = game.shoot(s, "a", "b", T0 + 5)
    assert s.status == "finished"
    assert s.winner_ids == ["a"] and s.end_reason == "last_standing" and s.ended_at == T0 + 5
    assert entries[-1].event_type == "game_over"
    with err("not_active"):
        game.move(s, "a", [0, 1], T0)


def test_host_removal_mid_game_kills_tank_and_can_end_game():
    s = make_active({"a": (0, 0), "b": (1, 0), "c": (5, 5)}, jury={"enabled": True})
    p(s, "c").ap = 9
    game.remove_player(s, "a", "c", T0)
    c = p(s, "c")
    assert not c.alive and c.ap == 0 and game.is_juror(s, c)
    assert p(s, "a").ap == 1  # no loot for removal
    game.remove_player(s, "a", "b", T0)
    assert s.status == "finished" and s.winner_ids == ["a"]


def test_only_host_can_remove():
    s = make_active({"a": (0, 0), "b": (1, 0), "c": (5, 5)})
    with err("forbidden"):
        game.remove_player(s, "b", "c", T0)


def test_time_limit_tiebreak_hp_then_ap_then_range():
    s = make_active({"a": (0, 0), "b": (5, 0), "c": (9, 9)}, max_duration_seconds=100)
    p(s, "a").hp, p(s, "b").hp, p(s, "c").hp = 4, 4, 2
    p(s, "a").ap, p(s, "b").ap = 3, 3
    p(s, "a").action_range, p(s, "b").action_range = 2, 3
    game.advance_time(s, T0 + 100, random.Random(0))
    assert s.status == "finished" and s.winner_ids == ["b"] and s.end_reason == "time_limit"


def test_time_limit_shared_win():
    s = make_active({"a": (0, 0), "b": (5, 0)}, max_duration_seconds=100)
    game.advance_time(s, T0 + 100, random.Random(0))
    assert sorted(s.winner_ids) == ["a", "b"]


# 5. Give AP / Give HP --------------------------------------------------------

def test_give_ap():
    s = make_active({"a": (0, 0), "b": (2, 2)})
    p(s, "a").ap = 5
    game.give_ap(s, "a", "b", 3, T0)
    assert p(s, "a").ap == 2 and p(s, "b").ap == 4


def test_give_ap_range_checked():
    s = make_active({"a": (0, 0), "b": (3, 3)})
    p(s, "a").ap = 5
    with err("out_of_range"):
        game.give_ap(s, "a", "b", 1, T0)


def test_give_ap_cannot_overspend_or_give_nonsense():
    s = make_active({"a": (0, 0), "b": (1, 1)})
    with err("insufficient_ap"):
        game.give_ap(s, "a", "b", 2, T0)
    for bad in (0, -1, 1.5, "1", True, None):
        with err("invalid_amount"):
            game.give_ap(s, "a", "b", bad, T0)
    with err("invalid_target"):
        game.give_ap(s, "a", "a", 1, T0)
    assert p(s, "a").ap == 1 and p(s, "b").ap == 1


def test_give_hp():
    s = make_active({"a": (0, 0), "b": (1, 1)})
    game.give_hp(s, "a", "b", 2, T0)
    assert p(s, "a").hp == 1 and p(s, "b").hp == 5
    assert p(s, "a").ap == 1  # costs no AP


def test_give_hp_cannot_self_kill():
    s = make_active({"a": (0, 0), "b": (1, 1)})
    with err("insufficient_hp"):
        game.give_hp(s, "a", "b", 3, T0)
    assert p(s, "a").hp == 3


def test_give_hp_range_checked_and_toggle():
    s = make_active({"a": (0, 0), "b": (4, 4)})
    with err("out_of_range"):
        game.give_hp(s, "a", "b", 1, T0)
    s2 = make_active({"a": (0, 0), "b": (1, 1)}, actions={"allow_give_hp": False})
    with err("disabled"):
        game.give_hp(s2, "a", "b", 1, T0)


def test_ap_cap_applies_to_gifts_and_ticks():
    s = make_active({"a": (0, 0), "b": (1, 1)}, ap={"cap": 3})
    p(s, "a").ap = 3
    p(s, "b").ap = 2
    game.give_ap(s, "a", "b", 3, T0)
    assert p(s, "b").ap == 3
    game.advance_time(s, T0 + DAY, random.Random(0))
    assert p(s, "a").ap == 1 and p(s, "b").ap == 3


# 6. AP ticks and catch-up ------------------------------------------------------

def test_ap_tick_catch_up_after_downtime():
    s = make_active({"a": (0, 0), "b": (5, 5), "c": (9, 0)})
    p(s, "c").alive = False
    entries = game.advance_time(s, T0 + 3 * DAY + 10, random.Random(0))
    assert p(s, "a").ap == 4 and p(s, "b").ap == 4
    assert p(s, "c").ap == 1  # dead players don't accrue
    ticks = [e for e in entries if e.event_type == "ap_tick"]
    assert len(ticks) == 3
    assert [e.ts for e in ticks] == [T0 + DAY, T0 + 2 * DAY, T0 + 3 * DAY]
    assert ticks[0].details == {"recipients": ["a", "b"]}
    assert s.last_ap_tick_at == T0 + 3 * DAY
    # Nothing more due until the next whole interval.
    assert game.advance_time(s, T0 + 4 * DAY - 1, random.Random(0)) == []


def test_debug_fast_ticks():
    s = make_active({"a": (0, 0), "b": (5, 5)}, debug={"fast_ticks": True, "tick_seconds": 10})
    game.advance_time(s, T0 + 25, random.Random(0))
    assert p(s, "a").ap == 3


def test_very_long_downtime_is_batched():
    s = make_active({"a": (0, 0), "b": (5, 5)}, ap={"interval_seconds": 1})
    entries = game.advance_time(s, T0 + 10_000, random.Random(0))
    assert p(s, "a").ap == 10_001
    assert len(entries) == 1 and entries[0].details["ticks"] == 10_000


# 7. Jury -----------------------------------------------------------------------

def _jury_game(**jury):
    s = make_active({"a": (0, 0), "b": (9, 9), "c": (0, 9), "d": (5, 5), "e": (9, 0)}, jury={"enabled": True, **jury})
    for pid in ("d", "e"):
        game.remove_player(s, "a", pid, T0)
    return s


def test_jury_single_vote_and_change_before_close():
    s = _jury_game()
    game.cast_jury_vote(s, "d", "b", T0 + 1)
    game.cast_jury_vote(s, "d", "c", T0 + 2)  # change of vote replaces, not adds
    assert len([v for v in s.jury_votes if v.juror_id == "d"]) == 1
    game.advance_time(s, T0 + DAY, random.Random(0))
    # c gets +1 from jury; all living get +1 from the AP tick.
    assert p(s, "c").ap == 3 and p(s, "b").ap == 2
    assert s.jury_cycle_index == 1 and s.jury_votes == []


def test_jury_tie_all():
    s = _jury_game()
    game.cast_jury_vote(s, "d", "b", T0)
    game.cast_jury_vote(s, "e", "c", T0)
    entries = game.advance_time(s, T0 + DAY, random.Random(0))
    assert p(s, "b").ap == 3 and p(s, "c").ap == 3 and p(s, "a").ap == 2
    award = next(e for e in entries if e.event_type == "jury_award")
    assert sorted(award.details["winners"]) == ["b", "c"]


def test_jury_tie_random():
    s = _jury_game(tie_rule="random")
    game.cast_jury_vote(s, "d", "b", T0)
    game.cast_jury_vote(s, "e", "c", T0)
    game.advance_time(s, T0 + DAY, random.Random(0))
    assert sorted([p(s, "b").ap, p(s, "c").ap]) == [2, 3]


def test_jury_no_votes_no_award():
    s = _jury_game()
    entries = game.advance_time(s, T0 + DAY, random.Random(0))
    assert not [e for e in entries if e.event_type == "jury_award"]
    assert all(p(s, x).ap == 2 for x in "abc")


def test_jury_vote_rules():
    s = _jury_game()
    with err("forbidden"):
        game.cast_jury_vote(s, "a", "b", T0)  # living players aren't jurors
    with err("invalid_target"):
        game.cast_jury_vote(s, "d", "e", T0)  # can't vote for the dead (incl. self)
    with err("invalid_target"):
        game.cast_jury_vote(s, "d", "d", T0)


def test_jury_disabled_rejects_votes_and_never_schedules():
    s = make_active({"a": (0, 0), "b": (9, 9), "c": (5, 5)})
    game.remove_player(s, "a", "c", T0)
    with err("disabled"):
        game.cast_jury_vote(s, "c", "a", T0)
    assert game.next_jury_close_at(s) is None and s.last_jury_cycle_at is None
    entries = game.advance_time(s, T0 + 5 * DAY, random.Random(0))
    assert {e.event_type for e in entries} == {"ap_tick"}
    assert game.view_for(s, "c")["jury"] is None
    # Dead players cannot affect the game.
    for fn in (lambda: game.move(s, "c", [5, 6], T0), lambda: game.shoot(s, "c", "a", T0),
               lambda: game.give_ap(s, "c", "a", 1, T0), lambda: game.give_hp(s, "c", "a", 1, T0),
               lambda: game.upgrade_range(s, "c", T0)):
        with err("dead"):
            fn()


def test_jury_catch_up_multiple_cycles():
    s = _jury_game()
    game.cast_jury_vote(s, "d", "b", T0)
    game.advance_time(s, T0 + 3 * DAY, random.Random(0))
    # Vote counts for the first cycle only; 3 AP ticks for everyone.
    assert p(s, "b").ap == 5 and p(s, "a").ap == 4
    assert s.jury_cycle_index == 3


# 8. Upgrade range ----------------------------------------------------------------

def test_upgrade_range():
    s = make_active({"a": (0, 0), "b": (3, 0)})
    p(s, "a").ap = 4
    game.upgrade_range(s, "a", T0)
    assert p(s, "a").action_range == 3 and p(s, "a").ap == 1
    game.shoot(s, "a", "b", T0)  # now in range


def test_upgrade_range_needs_ap_and_respects_cap():
    s = make_active({"a": (0, 0), "b": (3, 0)}, actions={"range_upgrade_cost": 2, "max_range": 3})
    with err("insufficient_ap"):
        game.upgrade_range(s, "a", T0)
    p(s, "a").ap = 10
    game.upgrade_range(s, "a", T0)
    assert p(s, "a").ap == 8
    with err("max_range"):
        game.upgrade_range(s, "a", T0)
    assert p(s, "a").action_range == 3 and p(s, "a").ap == 8


# 10. Fog of war --------------------------------------------------------------------

def _fog_scenario(fog: str):
    s = make_active({"a": (0, 0), "b": (5, 5), "c": (6, 5), "d": (9, 9)}, fog_of_war=fog)
    p(s, "b").ap = 5
    game.give_ap(s, "b", "c", 2, T0)  # gift between B and C
    game.give_hp(s, "c", "b", 1, T0)
    p(s, "d").ap = 4
    p(s, "c").hp = 1
    game.move(s, "d", [8, 8], T0)
    p(s, "d").action_range = 3
    game.shoot(s, "d", "c", T0)  # D kills C and loots
    game.advance_time(s, T0 + DAY, random.Random(0))
    return s


def test_fog_alliance_redacts_payload_for_player_a():
    s = _fog_scenario("alliance")
    view = game.view_for(s, "a")
    # Everything except the static game config (which has an "ap" settings section).
    blob = json.dumps({k: v for k, v in view.items() if k != "game"})
    # A sees only their own AP.
    for pl in view["players"]:
        assert ("ap" in pl) == (pl["id"] == "a")
    # No gifts between B and C, not even that they happened.
    assert not [e for e in view["log"] if e["event_type"] in ("give_ap", "give_hp")]
    # Kill is public, loot amount is not.
    assert [e for e in view["log"] if e["event_type"] == "kill"]
    assert not [e for e in view["log"] if e["event_type"] == "loot"]
    # AP ticks are public but list no balances.
    tick = next(e for e in view["log"] if e["event_type"] == "ap_tick")
    assert set(tick["details"]) == {"recipients"}
    assert '"ap"' not in blob.replace('"ap": %d' % p(s, "a").ap, "")


def test_fog_alliance_participants_see_their_gifts_and_killer_sees_loot():
    s = _fog_scenario("alliance")
    for viewer in ("b", "c"):
        log = game.view_for(s, viewer)["log"]
        gift = next(e for e in log if e["event_type"] == "give_ap")
        assert gift["actor_id"] == "b" and gift["target_id"] == "c" and gift["amount"] == 2
        assert [e for e in log if e["event_type"] == "give_hp"]
    d_log = game.view_for(s, "d")["log"]
    assert not [e for e in d_log if e["event_type"] in ("give_ap", "give_hp")]
    loot = next(e for e in d_log if e["event_type"] == "loot")
    assert loot["amount"] == 3  # C's starting 1 AP + the 2 gifted by B
    # The victim does not see the loot amount either.
    assert not [e for e in game.view_for(s, "c")["log"] if e["event_type"] == "loot"]
    # Anonymous viewers see nothing hidden and no AP at all.
    anon = game.view_for(s, None)
    assert all("ap" not in pl for pl in anon["players"])
    assert {e["visibility"] for e in anon["log"]} == {"public"}


def test_fog_off_everything_visible():
    s = _fog_scenario("off")
    view = game.view_for(s, "a")
    assert all("ap" in pl for pl in view["players"])
    types = {e["event_type"] for e in view["log"]}
    assert {"give_ap", "give_hp", "kill", "loot"} <= types
    assert len(view["log"]) == len(s.log)


def test_jury_votes_private_when_configured():
    s = _jury_game(votes_public=False)
    game.cast_jury_vote(s, "d", "b", T0)
    assert game.view_for(s, "a")["jury"]["votes"] == []
    assert not [e for e in game.view_for(s, "a")["log"] if e["event_type"] == "jury_vote"]
    assert game.view_for(s, "d")["jury"]["votes"] == [{"juror_id": "d", "candidate_id": "b"}]


def test_webhook_url_only_visible_to_host():
    url = "https://discord.com/api/webhooks/1/abc"
    s = make_active({"a": (0, 0), "b": (5, 5)}, notifications={"discord_webhook_url": url})
    assert game.view_for(s, "a")["game"]["config"]["notifications"]["discord_webhook_url"] == url
    assert game.view_for(s, "b")["game"]["config"]["notifications"]["discord_webhook_url"] is None


# Lobby / setup ---------------------------------------------------------------------

def test_start_requires_two_players_and_host():
    cfg = GameConfig()
    s = game.create_game("x", "h", "Host", cfg, T0)
    with err("not_enough_players"):
        game.start_game(s, "h", T0, random.Random(0))
    game.add_player(s, "p2", "Two", T0)
    with err("forbidden"):
        game.start_game(s, "p2", T0, random.Random(0))
    game.start_game(s, "h", T0, random.Random(0))
    assert s.status == "active" and s.board_size == 10
    with err("not_lobby"):
        game.add_player(s, "p3", "Three", T0)


def test_board_size_formula():
    cfg = GameConfig()
    s = game.create_game("x", "p0", "P0", cfg, T0)
    for i in range(1, 20):
        game.add_player(s, f"p{i}", f"P{i}", T0)
    game.start_game(s, "p0", T0, random.Random(0))
    assert s.board_size == 14  # ceil(sqrt(20) * 3)


def test_starting_positions_spaced_when_possible():
    for seed in range(20):
        spots = game.place_tanks(14, 20, random.Random(seed))
        assert len(set(spots)) == 20
        assert all(game.distance(a, b) >= 3 for i, a in enumerate(spots) for b in spots[i + 1:])


def test_starting_positions_relax_when_crowded():
    spots = game.place_tanks(4, 16, random.Random(0))
    assert len(set(spots)) == 16


def test_duplicate_names_rejected():
    s = game.create_game("x", "h", "Host", GameConfig(), T0)
    with err("invalid_name"):
        game.add_player(s, "p2", " host ", T0)


def test_config_validation():
    base = GameConfig()
    for bad in (
        {"nope": 1},                                     # unknown key (typo)
        {"fog_of_war": "total"},                         # not an allowed value
        {"notifications": {"discord_webhook_url": "https://evil.example/api/webhooks/1"}},
        {"start": {"hp": "5"}},                           # strict: no string -> int coercion
        {"start": {"hp": True}},                          # strict: bool is not an int
        {"start": {"hp": 0}},                             # below minimum
        {"start": {"hp": None}},                          # not nullable
        {"players": {"minimum": 10, "maximum": 5}},      # within-section check
        {"jury_enabled": True},                          # old flat key: unknown
        {"ap": {"cap": 2}, "start": {"ap": 3}},          # cross-section check
    ):
        with pytest.raises(ConfigError):
            base.with_overrides(bad)
    with pytest.raises(ConfigError):
        GameConfig.parse(["not", "a", "dict"])

    c = base.with_overrides({"ap": {"cap": None}, "start": {"hp": 5}})
    assert c.start.hp == 5 and c.ap.cap is None
    assert c.start.ap == 1 and c.start.action_range == 2  # partial override keeps sibling fields
    assert base.start.hp == 3  # immutable: the original is unchanged


def test_config_error_messages_are_readable():
    with pytest.raises(ConfigError) as e:
        GameConfig.parse({"start": {"hp": 0}, "fog_of_war": "x"})
    assert "start.hp" in str(e.value) and "fog_of_war" in str(e.value)
    with pytest.raises(ConfigError) as e:
        GameConfig.parse({"players": {"minimum": 10, "maximum": 5}})
    assert str(e.value) == "players: maximum must be >= minimum"


def test_config_derived_values_and_storage_round_trip():
    c = GameConfig.parse({"debug": {"fast_ticks": True, "tick_seconds": 7}})
    assert c.ap_interval == 7 and c.jury_interval == 7
    assert GameConfig().ap_interval == 86_400
    assert GameConfig().board.size_for(20) == 14 and GameConfig().board.size_for(2) == 10
    assert GameConfig.parse({"board": {"size": 6}}).board.size_for(20) == 6
    # Old saves load with new defaults filled in and retired keys ignored.
    old = {"start": {"hp": 4, "retired": 1}, "some_retired_section": {}}
    assert GameConfig.from_stored(old) == GameConfig.parse({"start": {"hp": 4}})


def test_update_config_in_lobby():
    s = game.create_game("x", "h", "Host", GameConfig(), T0)
    game.add_player(s, "p2", "Two", T0)
    game.update_config(s, "h", {"start": {"hp": 5, "ap": 2}, "fog_of_war": "alliance"}, T0)
    assert s.config.start.hp == 5 and s.config.fog_of_war == "alliance"
    assert all((p.hp, p.ap) == (5, 2) for p in s.players)  # joined players get the new starting stats
    with err("forbidden"):
        game.update_config(s, "p2", {"start": {"hp": 4}}, T0)
    game.add_player(s, "p3", "Three", T0)
    with err("lobby_full"):
        game.update_config(s, "h", {"players": {"maximum": 2}}, T0)  # 3 players already joined
    with pytest.raises(ConfigError):
        game.update_config(s, "h", {"start": {"hp": "lots"}}, T0)
    assert s.config.start.hp == 5  # rejected changes leave the config untouched
    game.start_game(s, "h", T0, random.Random(0))
    with err("not_lobby"):
        game.update_config(s, "h", {"start": {"hp": 4}}, T0)


# Leaving and closing a lobby ---------------------------------------------------------

def _lobby(*names):
    s = game.create_game("x", "h", "Host", GameConfig(), T0)
    for n in names:
        game.add_player(s, n, n.title(), T0)
    return s


def test_leave_lobby_removes_player_and_logs_it():
    s = _lobby("bob", "cat")
    entries = game.leave_lobby(s, "bob", T0 + 1)
    assert [pl.id for pl in s.players] == ["h", "cat"]
    assert [(e.event_type, e.actor_id, e.details) for e in entries] == [(game.EventType.LEAVE, "bob", {"name": "Bob"})]
    game.add_player(s, "bob2", "Bob", T0 + 2)  # the name is free again


def test_leave_lobby_rules():
    s = _lobby("bob")
    with err("forbidden"):
        game.leave_lobby(s, "h", T0)  # the host closes the lobby instead
    with err("not_found"):
        game.leave_lobby(s, "nobody", T0)
    game.start_game(s, "h", T0, random.Random(0))
    with err("not_lobby"):
        game.leave_lobby(s, "bob", T0)  # no leaving once the game is running


def test_close_lobby():
    s = _lobby("bob")
    with err("forbidden"):
        game.close_lobby(s, "bob", T0)
    entries = game.close_lobby(s, "h", T0 + 5)
    assert s.status == "closed" and s.ended_at == T0 + 5
    assert [(e.event_type, e.actor_id) for e in entries] == [(game.EventType.LOBBY_CLOSED, "h")]
    assert game.next_due_at(s) is None and game.advance_time(s, T0 + 100 * DAY, random.Random(0)) == []


def test_closed_lobby_is_final():
    s = _lobby("bob")
    game.close_lobby(s, "h", T0)
    for attempt in (
        lambda: game.add_player(s, "cat", "Cat", T0),
        lambda: game.start_game(s, "h", T0, random.Random(0)),
        lambda: game.update_config(s, "h", {"start": {"hp": 5}}, T0),
        lambda: game.leave_lobby(s, "bob", T0),
        lambda: game.close_lobby(s, "h", T0),
        lambda: game.move(s, "bob", [1, 1], T0),
    ):
        with err("closed") as info:
            attempt()
        assert info.value.message == "The host closed this lobby."
    assert s.status == "closed"


def test_cannot_close_a_started_game():
    s = _lobby("bob")
    game.start_game(s, "h", T0, random.Random(0))
    with err("not_lobby"):
        game.close_lobby(s, "h", T0)
