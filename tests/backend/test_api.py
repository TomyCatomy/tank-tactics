"""HTTP + WebSocket API tests (FastAPI TestClient), including fog of war on the wire."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from ..support import place


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
    for path in ("/static/app.js", "/static/style.css"):
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
    assert state["game"]["config"]["start"]["action_range"] == 2


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
