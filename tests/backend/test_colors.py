"""Tank colours: the palette new players get, and choosing your own in the lobby."""
from __future__ import annotations

import pytest

from tank_tactics import game
from tank_tactics.config import GameConfig

from ..support import T0, make_active, make_service
from .test_api import auth, new_game


def lobby(*names: str) -> game.GameState:
    s = game.create_game("g", "a", names[0], GameConfig(), T0)
    for i, n in enumerate(names[1:]):
        game.add_player(s, f"p{i}", n, T0)
    return s


def test_new_players_get_distinct_palette_colors():
    s = lobby("Ann", "Bob", "Cat")
    assert [p.color for p in s.players] == game.PLAYER_COLORS[:3]


def test_pick_a_color_in_the_lobby():
    s = lobby("Ann", "Bob")
    assert game.set_color(s, "a", "#00CED1", T0) == []  # nothing is logged
    assert game.get_player(s, "a").color == "#00ced1"
    game.set_color(s, "a", "#00ced1", T0)  # choosing your own colour again is fine
    # A colour Ann gave up is free for others.
    game.set_color(s, "p0", game.PLAYER_COLORS[0], T0)
    assert game.get_player(s, "p0").color == game.PLAYER_COLORS[0]


@pytest.mark.parametrize(("color", "code"), [
    ("#e6194c", "color_taken"),  # a hair away from Ann's #e6194b
    ("#000000", "invalid_color"), ("#0a0a14", "invalid_color"),  # too dark for the board
    ("red", "invalid_color"), ("#abc", "invalid_color"), ("#12345g", "invalid_color"), (None, "invalid_color"),
    ("#1234567", "invalid_color"),
])
def test_bad_or_clashing_colors_are_refused(color, code):
    s = lobby("Ann", "Bob")
    before = game.get_player(s, "p0").color
    with pytest.raises(game.GameError) as e:
        game.set_color(s, "p0", color, T0)
    assert e.value.code == code
    assert game.get_player(s, "p0").color == before


def test_color_is_fixed_once_the_game_starts():
    s = make_active({"a": (0, 0), "b": (3, 3)})
    with pytest.raises(game.GameError) as e:
        game.set_color(s, "a", "#00ced1", T0)
    assert e.value.code == "not_lobby"


def test_color_endpoint_and_persistence(api, db):
    client, _, _ = api
    ann, bob = new_game(client, names=("Ann", "Bob"))
    gid = ann["game_id"]
    assert client.post(f"/api/games/{gid}/color", headers=auth(bob), json={"color": "#00ced1"}).status_code == 200
    state = client.get(f"/api/games/{gid}/state", headers=auth(ann)).json()
    assert next(p for p in state["players"] if p["id"] == bob["player_id"])["color"] == "#00ced1"
    r = client.post(f"/api/games/{gid}/color", headers=auth(ann), json={"color": "#00cfd2"})
    assert r.status_code == 400 and r.json()["error"] == {"code": "color_taken", "message": "That colour is too close to Bob's."}
    assert client.post(f"/api/games/{gid}/color", headers=auth(ann), json={}).status_code == 400
    assert client.get("/api/defaults").json()["colors"] == game.PLAYER_COLORS

    svc, clock = make_service(db)
    host = svc.create_game("Ann")
    svc.set_color(host["game_id"], host["player_id"], "#00ced1")
    svc.store.close()
    svc2, _ = make_service(db, clock)
    assert svc2.get(host["game_id"]).players[0].color == "#00ced1"
    svc2.store.close()
