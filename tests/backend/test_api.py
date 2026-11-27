"""HTTP + WebSocket API tests (FastAPI TestClient), including fog of war on the wire."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tank_tactics.api import create_app
from tank_tactics.config import GameConfig

from ..support import make_service, place


def auth(p: dict) -> dict:
    return {"Authorization": f"Bearer {p['token']}"}


def new_game(client: TestClient, names=("Ann", "Bob", "Cat"), config=None) -> list[dict]:
    r = client.post("/api/games", json={"name": names[0], "config": config})
    assert r.status_code == 201, r.text
    host = r.json()
    others = [client.post(f"/api/games/{host['game_id']}/join", json={"name": n}).json() for n in names[1:]]
    return [host, *others]


def test_fog_payload_and_actions(api):
    client, svc, _ = api
    ann, bob, cat = new_game(client, config={"fog_of_war": "alliance"})
    gid = ann["game_id"]

    r = client.post(f"/api/games/{gid}/start", headers=auth(bob))
    assert r.status_code == 400 and r.json()["error"]["code"] == "forbidden"
    assert client.post(f"/api/games/{gid}/start", headers=auth(ann)).status_code == 200

    place(svc, gid, {ann["player_id"]: (0, 0), bob["player_id"]: (5, 5), cat["player_id"]: (6, 6)})
    r = client.post(f"/api/games/{gid}/action", headers=auth(bob),
                    json={"type": "give_ap", "target_id": cat["player_id"], "amount": 1})
    assert r.status_code == 200, r.text

    # Player A's payload: no other player's AP, no B -> C gift.
    state_a = client.get(f"/api/games/{gid}/state", headers=auth(ann)).json()
    for pl in state_a["players"]:
        assert ("ap" in pl) == (pl["id"] == ann["player_id"])
    assert "give_ap" not in json.dumps(state_a)

    # The recipient does see it.
    state_c = client.get(f"/api/games/{gid}/state", headers=auth(cat)).json()
    assert [e for e in state_c["log"] if e["event_type"] == "give_ap"]
    assert next(p for p in state_c["players"] if p["id"] == cat["player_id"])["ap"] == 2


def test_websocket_pushes_redacted_state(api):
    client, svc, _ = api
    ann, bob, cat = new_game(client, config={"fog_of_war": "alliance"})
    gid = ann["game_id"]
    client.post(f"/api/games/{gid}/start", headers=auth(ann))
    place(svc, gid, {ann["player_id"]: (0, 0), bob["player_id"]: (5, 5), cat["player_id"]: (6, 6)})

    with client.websocket_connect(f"/ws/{gid}") as ws:
        ws.send_json({"type": "auth", "token": ann["token"]})
        first = ws.receive_json()
        assert first["type"] == "state" and first["state"]["viewer_id"] == ann["player_id"]

        # A hidden gift between B and C still triggers a push, but its content isn't in A's view.
        client.post(f"/api/games/{gid}/action", headers=auth(bob),
                    json={"type": "give_ap", "target_id": cat["player_id"], "amount": 1})
        pushed = ws.receive_json()
        assert "give_ap" not in json.dumps(pushed)

        client.post(f"/api/games/{gid}/action", headers=auth(cat), json={"type": "move", "to": [7, 7]})
        pushed = ws.receive_json()
        assert any(e["event_type"] == "move" for e in pushed["state"]["log"])

        ws.send_text("ping")
        assert ws.receive_text() == "pong"


def test_websocket_rejects_bad_token(api):
    client, _, _ = api
    ann, *_ = new_game(client, names=("Ann", "Bob"))
    with client.websocket_connect(f"/ws/{ann['game_id']}") as ws:
        ws.send_json({"type": "auth", "token": "nope"})
        msg = ws.receive_json()
        assert msg["type"] == "error" and msg["error"]["code"] == "unauthorized"


def test_error_format(api):
    client, _, _ = api
    ann, bob = new_game(client, names=("Ann", "Bob"))
    gid = ann["game_id"]

    r = client.get(f"/api/games/{gid}/state", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    r = client.get(f"/api/games/{gid}/state")
    assert r.status_code == 401

    r = client.post(f"/api/games/{gid}/action", headers={**auth(ann), "Content-Type": "application/json"},
                    content="not json")
    assert r.status_code == 400 and r.json()["error"] == {"code": "bad_request", "message": "Body must be valid JSON."}

    r = client.post(f"/api/games/{gid}/action", headers=auth(ann), json={"type": "nuke"})
    assert r.status_code == 400 and r.json()["error"] == {
        "code": "bad_request", "message": "'type' must be one of: move, shoot, give_ap, give_hp, upgrade"}
    r = client.post(f"/api/games/{gid}/action", headers=auth(ann), json={"type": "move", "to": [1]})
    assert r.status_code == 400 and "to" in r.json()["error"]["message"]

    r = client.post(f"/api/games/{gid}/action", headers=auth(ann), json={"type": "upgrade"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_active"  # game rules, not schema

    r = client.post("/api/games", json={"name": "X", "config": {"jury": {"enabled": "yes"}}})
    assert r.status_code == 400 and r.json()["error"] == {
        "code": "invalid_config", "message": "jury.enabled: Input should be a valid boolean"}

    r = client.get("/api/games/doesnotexist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"

    r = client.post(f"/api/games/{gid}/chat", headers=auth(ann), content=b"x" * (65 * 1024))
    assert r.status_code == 413


def test_lobby_flow_info_config_remove_chat(api):
    client, _, _ = api
    ann, bob, cat = new_game(client)
    gid = ann["game_id"]
    info = client.get(f"/api/games/{gid}").json()
    assert info == {"id": gid, "status": "lobby", "host": "Ann", "player_count": 3, "max_players": 64}

    assert client.post(f"/api/games/{gid}/config", headers=auth(ann), json={"config": {"start": {"hp": 5}}}).status_code == 200
    state = client.get(f"/api/games/{gid}/state", headers=auth(bob)).json()
    assert state["game"]["config"]["start"]["hp"] == 5

    assert client.post(f"/api/games/{gid}/remove", headers=auth(ann), json={"player_id": cat["player_id"]}).status_code == 200
    assert client.get(f"/api/games/{gid}/state", headers=auth(cat)).status_code == 401

    r = client.post(f"/api/games/{gid}/chat", headers=auth(bob), json={"to_id": ann["player_id"], "text": "ally?"})
    assert r.status_code == 201 and r.json()["text"] == "ally?"


def test_static_and_defaults(api):
    client, _, _ = api
    assert client.get("/api/defaults").json()["config"]["fog_of_war"] == "off"
    r = client.get("/")
    assert r.status_code == 200 and "Tank Tactics" in r.text
    for path in ("/static/js/main.js", "/static/style.css"):
        assert client.get(path).status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "Tank Tactics"


def test_payload_field_names(api):
    """The web client reads these exact keys; renaming them breaks the UI."""
    client, _, _ = api
    ann, _bob = new_game(client, names=("Ann", "Bob"))
    gid = ann["game_id"]
    client.post(f"/api/games/{gid}/start", headers=auth(ann))
    state = client.get(f"/api/games/{gid}/state", headers=auth(ann)).json()
    for pl in state["players"]:
        assert "action_range" in pl and "range" not in pl
    for entry in state["log"]:
        assert "event_type" in entry and "type" not in entry
    start = next(e for e in state["log"] if e["event_type"] == "game_start")
    assert set(start["details"]) == {"board_size", "positions", "hp", "action_range"}
    assert state["game"]["config"]["start"]["action_range"] == 3
    # The lobby's schedule editor reads and writes these.
    assert set(state["game"]["config"]["ap"]) == {"rrule", "timezone", "cap"}
    sched = state["game"]["schedule"]
    assert set(sched) == {"rrule", "timezone", "summary", "effective_rrule", "effective_timezone",
                          "effective_summary"}
    d = client.get("/api/defaults").json()
    assert "Asia/Jerusalem" in d["timezones"] and d["config"]["ap"]["rrule"] == "FREQ=DAILY"


def test_leave_and_close_endpoints(api):
    client, _, _ = api
    ann, bob, cat = new_game(client)
    gid = ann["game_id"]

    r = client.post(f"/api/games/{gid}/leave", headers=auth(ann))
    assert r.status_code == 400 and r.json()["error"]["code"] == "forbidden"  # host must close instead
    assert client.post(f"/api/games/{gid}/leave", headers=auth(bob)).status_code == 200
    assert client.get(f"/api/games/{gid}/state", headers=auth(bob)).status_code == 401

    r = client.post(f"/api/games/{gid}/close", headers=auth(cat))
    assert r.status_code == 400 and r.json()["error"]["code"] == "forbidden"
    assert client.post(f"/api/games/{gid}/close", headers=auth(ann)).status_code == 200
    assert client.get(f"/api/games/{gid}").json()["status"] == "closed"
    state = client.get(f"/api/games/{gid}/state", headers=auth(cat)).json()
    assert state["game"]["status"] == "closed" and state["log"][-1]["event_type"] == "lobby_closed"

    r = client.post(f"/api/games/{gid}/join", json={"name": "Dan"})
    assert r.status_code == 400 and r.json()["error"] == {"code": "closed", "message": "The host closed this lobby."}
    r = client.post(f"/api/games/{gid}/start", headers=auth(ann))
    assert r.status_code == 400 and r.json()["error"]["code"] == "closed"


def _ws_auth(client, player):
    ws = client.websocket_connect(f"/ws/{player['game_id']}")
    ws.__enter__()
    ws.send_json({"type": "auth", "token": player["token"]})
    assert ws.receive_json()["type"] == "state"
    return ws


def test_websocket_tells_departed_players_they_are_out(api):
    """Regression: removed/departed lobby players used to keep receiving anonymous pushes."""
    client, _, _ = api
    ann, bob, cat = new_game(client)
    gid = ann["game_id"]
    ws_bob, ws_cat, ws_ann = _ws_auth(client, bob), _ws_auth(client, cat), _ws_auth(client, ann)
    try:
        client.post(f"/api/games/{gid}/leave", headers=auth(bob))
        assert ws_bob.receive_json() == {"type": "error", "error": {
            "code": "not_in_game", "message": "You are no longer in this game."}}
        assert [p["name"] for p in ws_ann.receive_json()["state"]["players"]] == ["Ann", "Cat"]
        ws_cat.receive_json()

        client.post(f"/api/games/{gid}/remove", headers=auth(ann), json={"player_id": cat["player_id"]})
        assert ws_cat.receive_json()["error"]["code"] == "not_in_game"
        ws_ann.receive_json()

        client.post(f"/api/games/{gid}/close", headers=auth(ann))
        assert ws_ann.receive_json()["state"]["game"]["status"] == "closed"
    finally:
        for ws in (ws_bob, ws_cat, ws_ann):
            try:
                ws.__exit__(None, None, None)
            except Exception:
                pass


# -- ALLOW_DEBUG ----------------------------------------------------------------------

@pytest.fixture
def locked_api():
    svc, clock = make_service(action_rate_per_second=100, allow_debug=False)
    with TestClient(create_app(svc)) as client:
        yield client, svc, clock


@pytest.mark.parametrize("debug", [{"fast_ticks": True}, {"local_seats": True}, {"tick_seconds": 5}])
def test_debug_options_rejected_when_not_allowed(locked_api, debug):
    client, _, _ = locked_api
    assert client.get("/api/defaults").json()["allow_debug"] is False
    r = client.post("/api/games", json={"name": "Ann", "config": {"debug": debug}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_config"

    host = client.post("/api/games", json={"name": "Ann"}).json()
    r = client.post(f"/api/games/{host['game_id']}/config", json={"config": {"debug": debug}}, headers=auth(host))
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_config"
    state = client.get(f"/api/games/{host['game_id']}/state", headers=auth(host)).json()
    assert state["allow_debug"] is False
    assert state["game"]["config"]["debug"] == GameConfig().debug.model_dump()


def test_existing_debug_game_survives_turning_the_flag_off(tmp_path):
    path = str(tmp_path / "g.db")
    on, _ = make_service(path, allow_debug=True)
    host = on.create_game("Ann", {"debug": {"fast_ticks": True}})

    off, _ = make_service(path, allow_debug=False)
    off.update_config(host["game_id"], host["player_id"], {"jury": {"enabled": True}})  # unrelated edits still work
    assert off.get(host["game_id"]).config.debug.fast_ticks is True
    off.update_config(host["game_id"], host["player_id"], {"debug": {"fast_ticks": False}})  # and it can be turned off
    assert off.get(host["game_id"]).config.debug.fast_ticks is False


# -- schedule preview ------------------------------------------------------------------------

def test_schedule_preview(api):
    client, _, clock = api
    clock.t = 1_774_432_800.0  # 2026-03-25 12:00 in Jerusalem, two days before DST starts
    r = client.post("/api/schedule/preview", json={"rrule": "freq=daily;byhour=9;byminute=0", "timezone": "Asia/Jerusalem"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rrule"] == "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    assert body == {"rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "timezone": "Asia/Jerusalem",
                    "summary": "Every day at 09:00 (Asia/Jerusalem)"}

    for rule, tz, needle in [("FREQ=DAILY;COUNT=2", "UTC", "rrule: COUNT is not allowed"),
                             ("FREQ=DAILY", "Nowhere/Land", "timezone: unknown time zone"),
                             ("FREQ=MONTHLY;BYMONTHDAY=30;BYMONTH=2", "UTC", "rrule: never fires")]:
        r = client.post("/api/schedule/preview", json={"rrule": rule, "timezone": tz})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_schedule", r.text
        assert needle in r.json()["error"]["message"]
    r = client.post("/api/schedule/preview", json={"rrule": "FREQ=DAILY"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


@pytest.mark.parametrize("which", ["debug", "locked"])
def test_schedule_preview_refuses_seconds_and_skipped_months(api, locked_api, which):
    client = (api if which == "debug" else locked_api)[0]
    for rule, needle in [("FREQ=SECONDLY;INTERVAL=10", "FREQ=SECONDLY is not allowed"),
                         ("FREQ=HOURLY;BYSECOND=30", "BYSECOND can only be 0"),
                         ("FREQ=MONTHLY;BYMONTHDAY=31", "Use BYMONTHDAY=-1"),
                         ("FREQ=YEARLY", "less than once a month")]:
        r = client.post("/api/schedule/preview", json={"rrule": rule, "timezone": "UTC"})
        assert r.status_code == 400 and needle in r.json()["error"]["message"], r.text
    r = client.post("/api/schedule/preview", json={"rrule": "FREQ=MONTHLY;BYDAY=TH;BYSETPOS=-1", "timezone": "UTC"})
    assert r.status_code == 200
