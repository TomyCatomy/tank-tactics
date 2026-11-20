"""Service, persistence, concurrency and chat tests (no HTTP)."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tank_tactics import game
from tank_tactics.service import ServiceError

from ..support import DAY, T0, make_service, place, setup_game

# 6. Tick catch-up across a real restart -----------------------------------------

def test_ap_catch_up_after_restart(tmp_path):
    db = str(tmp_path / "t.db")
    svc, clock = make_service(db)
    gid, ids, _ = setup_game(svc)
    svc.store.close()

    clock.t += 3 * DAY + 5  # server was down for 3 intervals
    svc2, _ = make_service(db, clock)
    assert all(p.ap == 1 for p in svc2.games[gid].players)
    svc2.tick()  # the boot-time scheduler pass
    assert all(p.ap == 4 for p in svc2.games[gid].players)
    assert svc2.games[gid].last_ap_tick_at == T0 + 3 * DAY
    svc2.store.close()

    # And it was persisted.
    svc3, _ = make_service(db, clock)
    assert all(p.ap == 4 for p in svc3.games[gid].players)
    assert len([e for e in svc3.games[gid].log if e.event_type == "ap_tick"]) == 3


def test_full_state_round_trips_through_db(tmp_path):
    db = str(tmp_path / "t.db")
    svc, clock = make_service(db)
    gid, (a, b, c), players = setup_game(svc, config={"jury": {"enabled": True}, "fog_of_war": "alliance"})
    place(svc, gid, {a: (0, 0), b: (1, 1), c: (9, 9)})
    svc.act(gid, b, {"type": "give_hp", "target_id": a, "amount": 1})
    svc.remove_player(gid, a, c)
    svc.vote(gid, c, b)
    svc.send_message(gid, a, b, "psst")
    before = svc.view(gid, a)
    svc.store.close()
    svc2, _ = make_service(db, clock)
    after = svc2.view(gid, a)
    assert json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)
    # Tokens still work after restart.
    assert svc2.authenticate(gid, players[1]["token"]) == b


# 9. Concurrency ----------------------------------------------------------------

def test_concurrent_moves_to_same_tile_one_wins():
    svc, _ = make_service(action_rate_per_second=1000)
    gid, (a, b, c), _ = setup_game(svc)
    place(svc, gid, {a: (0, 0), b: (2, 0), c: (9, 9)})
    barrier = threading.Barrier(2)

    def go(pid):
        barrier.wait()
        try:
            svc.act(gid, pid, {"type": "move", "to": [1, 0]})
            return "ok"
        except ServiceError as e:
            return e.code

    with ThreadPoolExecutor(2) as ex:
        results = sorted(ex.map(go, [a, b]))
    assert results == ["occupied", "ok"]
    occupants = [p for p in svc.games[gid].players if p.pos == (1, 0)]
    assert len(occupants) == 1


def test_concurrent_shots_on_last_hp_one_kill():
    svc, _ = make_service(action_rate_per_second=1000)
    gid, (a, b, c), _ = setup_game(svc)
    place(svc, gid, {a: (0, 0), b: (2, 0), c: (1, 0)})
    game.get_player(svc.games[gid], c).hp = 1
    barrier = threading.Barrier(2)

    def go(pid):
        barrier.wait()
        try:
            svc.act(gid, pid, {"type": "shoot", "target_id": c})
            return "ok"
        except ServiceError as e:
            return e.code

    with ThreadPoolExecutor(2) as ex:
        results = sorted(ex.map(go, [a, b]))
    assert results == ["invalid_target", "ok"]
    assert len([e for e in svc.games[gid].log if e.event_type == "kill"]) == 1
    assert svc.games[gid].status == "active"  # two tanks still alive
    assert len([e for e in svc.games[gid].log if e.event_type == "shoot"]) == 1


def test_failed_db_write_leaves_state_untouched():
    svc, _ = make_service()
    gid, (a, b, c), _ = setup_game(svc)
    place(svc, gid, {a: (0, 0), b: (5, 5), c: (9, 9)})

    def boom(*args, **kw):
        raise RuntimeError("disk full")

    svc.store.save = boom
    with pytest.raises(RuntimeError):
        svc.act(gid, a, {"type": "move", "to": [1, 0]})
    assert game.get_player(svc.games[gid], a).pos == (0, 0)
    assert game.get_player(svc.games[gid], a).ap == 1


# Misc service behaviour -----------------------------------------------------------

def test_rate_limit():
    svc, _ = make_service(action_rate_per_second=5)
    gid, (a, b, c), _ = setup_game(svc)
    place(svc, gid, {a: (0, 0), b: (5, 5), c: (9, 9)})
    codes = []
    for _ in range(7):
        try:
            svc.act(gid, a, {"type": "upgrade"})
        except ServiceError as e:
            codes.append(e.code)
    assert codes.count("rate_limited") == 2


def test_chat_privacy_and_dead_players_public_only():
    svc, _ = make_service()
    gid, (a, b, c), _ = setup_game(svc)
    svc.send_message(gid, a, None, "hello all")
    svc.send_message(gid, a, b, "secret pact")
    assert [m["text"] for m in svc.visible_messages(gid, b)] == ["hello all", "secret pact"]
    assert [m["text"] for m in svc.visible_messages(gid, c)] == ["hello all"]
    assert [m["text"] for m in svc.visible_messages(gid, None)] == ["hello all"]

    svc.remove_player(gid, a, b)  # b's tank destroyed
    assert [m["text"] for m in svc.visible_messages(gid, b)] == ["hello all"]
    with pytest.raises(ServiceError) as e:
        svc.send_message(gid, b, c, "help")
    assert e.value.code == "forbidden"
    with pytest.raises(ServiceError):
        svc.send_message(gid, c, b, "you there?")
    svc.send_message(gid, b, None, "gg")  # public still works


def test_lobby_removal_revokes_token():
    svc, _ = make_service()
    host = svc.create_game("Ann")
    gid = host["game_id"]
    bob = svc.join_game(gid, "Bob")
    svc.remove_player(gid, host["player_id"], bob["player_id"])
    with pytest.raises(ServiceError) as e:
        svc.authenticate(gid, bob["token"])
    assert e.value.status == 401


def test_jury_disabled_scheduler_never_closes_cycles():
    svc, clock = make_service()
    gid, (a, b, c), _ = setup_game(svc)
    clock.t += 10 * DAY
    svc.tick()
    s = svc.games[gid]
    assert s.last_jury_cycle_at is None and s.jury_cycle_index == 0
    assert not [e for e in s.log if e.event_type.startswith("jury")]


def test_host_updates_lobby_config_and_it_persists(tmp_path):
    db = str(tmp_path / "t.db")
    svc, clock = make_service(db)
    host = svc.create_game("Ann")
    gid = host["game_id"]
    svc.update_config(gid, host["player_id"], {"jury": {"enabled": True}, "ap": {"interval_seconds": 3600}})
    with pytest.raises(ServiceError) as e:
        svc.update_config(gid, host["player_id"], {"nope": 1})
    assert e.value.code == "invalid_config"
    svc.store.close()
    svc2, _ = make_service(db, clock)
    cfg = svc2.games[gid].config
    assert cfg.jury.enabled and cfg.ap.interval_seconds == 3600


def test_act_validates_raw_payloads():
    svc, _ = make_service()
    gid, (a, b, c), _ = setup_game(svc)
    for bad in ({"type": "nuke"}, {"type": "move", "to": [1]}, {"type": "give_ap", "target_id": b, "amount": "2"}, "x"):
        with pytest.raises(ServiceError) as e:
            svc.act(gid, a, bad)
        assert e.value.code == "invalid_action"
    place(svc, gid, {a: (0, 0), b: (1, 1), c: (9, 9)})
    svc.act(gid, a, game.GiveApCommand(type=game.ActionType.GIVE_AP, target_id=b, amount=1))  # typed command
    assert game.get_player(svc.games[gid], b).ap == 2


def test_leave_revokes_token_and_close_persists(tmp_path):
    db = str(tmp_path / "t.db")
    svc, clock = make_service(db)
    host = svc.create_game("Ann")
    gid = host["game_id"]
    bob = svc.join_game(gid, "Bob")
    cat = svc.join_game(gid, "Cat")

    svc.leave_game(gid, bob["player_id"])
    with pytest.raises(ServiceError) as e:
        svc.authenticate(gid, bob["token"])
    assert e.value.status == 401
    assert svc.authenticate(gid, cat["token"]) == cat["player_id"]  # nobody else affected

    svc.close_game(gid, host["player_id"])
    # Tokens survive a close, so players can still open the game and see that it was closed.
    assert svc.authenticate(gid, host["token"]) == host["player_id"]
    assert svc.public_info(gid)["status"] == "closed"
    with pytest.raises(ServiceError) as e:
        svc.join_game(gid, "Dan")
    assert e.value.code == "closed"

    svc.store.close()
    svc2, _ = make_service(db, clock)
    restored = svc2.get(gid)
    assert restored.status == "closed" and [p.name for p in restored.players] == ["Ann", "Cat"]
    assert restored.log[-1].event_type == game.EventType.LOBBY_CLOSED


def test_mid_game_removal_keeps_token_for_spectating():
    svc, _ = make_service()
    gid, (a, b, c), players = setup_game(svc)
    svc.remove_player(gid, a, b)
    assert svc.authenticate(gid, players[1]["token"]) == b  # still in the game, as a spectator


# Finished games leave memory --------------------------------------------------

def _finish(svc, gid, a, b):
    """End a two-player game: ``a`` shoots ``b``'s last hit point."""
    place(svc, gid, {a: (0, 0), b: (1, 0)})
    game.get_player(svc.games[gid], b).hp = 1
    svc.act(gid, a, {"type": "shoot", "target_id": b})


def test_finished_game_is_evicted_but_still_served(tmp_path):
    db = str(tmp_path / "t.db")
    svc, clock = make_service(db)
    gid, (a, b), creds = setup_game(svc, names=("Ann", "Bob"))
    svc.send_message(gid, a, None, "gg")
    _finish(svc, gid, a, b)

    assert gid not in svc.games and gid not in svc.messages
    view = svc.view(gid, a)
    assert view["game"]["status"] == "finished" and view["game"]["winner_ids"] == [a]
    svc.send_message(gid, b, None, "well played")  # post-game chat still works
    assert [m["text"] for m in svc.view(gid, b)["messages"]] == ["gg", "well played"]
    svc.store.close()

    svc2, _ = make_service(db, clock)
    assert gid not in svc2.games  # not loaded on boot
    assert svc2.get(gid).status == "finished"
    assert [m["text"] for m in svc2.view(gid, a)["messages"]] == ["gg", "well played"]
    assert svc2.authenticate(gid, creds[0]["token"]) == a
    svc2.store.close()


def test_archive_cache_is_bounded():
    svc, _ = make_service(archive_cache_size=1)
    games = [setup_game(svc, names=("Ann", "Bob")) for _ in range(3)]
    for gid, (a, b), _ in games:
        _finish(svc, gid, a, b)
    assert len(svc._archive) == 1
    for gid, (a, _b), _ in games:  # evicted ones are re-read from the database
        assert svc.view(gid, a)["game"]["status"] == "finished"
    assert len(svc._archive) == 1
    assert svc.games == {}


def test_closed_lobby_is_evicted():
    svc, _ = make_service()
    host = svc.create_game("Ann")
    svc.close_game(host["game_id"], host["player_id"])
    assert host["game_id"] not in svc.games
    assert svc.public_info(host["game_id"])["status"] == "closed"


def test_unknown_game_is_not_found():
    svc, _ = make_service()
    assert svc.get("nope") is None
    with pytest.raises(ServiceError) as e:
        svc.view("nope", None)
    assert e.value.code == "not_found"
