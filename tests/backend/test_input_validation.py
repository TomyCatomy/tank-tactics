"""Malformed client strings are clean 4xx errors, never a crash or a stored value."""
from __future__ import annotations

import json

import pytest
from starlette.websockets import WebSocketDisconnect

from tank_tactics.config import GameConfig

SURROGATE = "bad\ud800name"
BAD_WEBHOOK = f"https://discord.com/api/webhooks/1/{SURROGATE}"


def post_raw(client, path: str, body: dict, token: str | None = None):
    """Send ``body`` with its JSON escapes intact; httpx can't encode lone surrogates itself."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(path, content=json.dumps(body).encode(), headers=headers)


def host_and_guest(client, config=None) -> tuple[dict, dict]:
    host = client.post("/api/games", json={"name": "Ann", "config": config}).json()
    guest = client.post(f"/api/games/{host['game_id']}/join", json={"name": "Bob"}).json()
    return host, guest


def assert_bad_request(r, code: str = "bad_request") -> None:
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == code


def test_create_rejects_surrogate_name(api):
    client, svc, _ = api
    assert_bad_request(post_raw(client, "/api/games", {"name": SURROGATE}))
    assert svc.games == {}


def test_join_rejects_surrogate_name(api):
    client, svc, _ = api
    host, _ = host_and_guest(client)
    assert_bad_request(post_raw(client, f"/api/games/{host['game_id']}/join", {"name": SURROGATE}))
    assert len(svc.get(host["game_id"]).players) == 2


def test_chat_rejects_surrogate_text(api):
    client, svc, _ = api
    host, _ = host_and_guest(client)
    gid = host["game_id"]
    for text in ("\ud800", SURROGATE):
        assert_bad_request(post_raw(client, f"/api/games/{gid}/chat", {"text": text}, host["token"]))
    assert svc.visible_messages(gid, host["player_id"]) == []


def test_valid_unicode_is_still_accepted(api):
    client, _, _ = api
    r = client.post("/api/games", json={"name": "🙂 Tänk 坦克"})
    assert r.status_code == 201
    host = r.json()
    r = post_raw(client, f"/api/games/{host['game_id']}/chat", {"text": "🙂 坦克"}, host["token"])
    assert r.status_code == 201 and r.json()["text"] == "🙂 坦克"


def test_webhook_with_surrogate_is_rejected(api):
    client, _, _ = api
    config = {"notifications": {"discord_webhook_url": BAD_WEBHOOK}}
    assert_bad_request(post_raw(client, "/api/games", {"name": "Ann", "config": config}), "invalid_config")

    host, _ = host_and_guest(client)
    gid = host["game_id"]
    r = post_raw(client, f"/api/games/{gid}/config", {"config": config}, host["token"])
    assert_bad_request(r, "invalid_config")
    state = client.get(f"/api/games/{gid}/state", headers={"Authorization": f"Bearer {host['token']}"})
    assert state.status_code == 200


def test_game_saved_with_a_bad_webhook_still_loads():
    cfg = GameConfig.from_stored({"notifications": {"discord_webhook_url": BAD_WEBHOOK}})
    assert cfg.notifications.discord_webhook_url == BAD_WEBHOOK


def test_surrogate_ids_are_rejected_in_an_active_game(api):
    client, _, _ = api
    host, guest = host_and_guest(client, {"jury": {"enabled": True}})
    gid, token = host["game_id"], host["token"]
    assert client.post(f"/api/games/{gid}/start", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    for path, body in (
        ("action", {"type": "shoot", "target_id": SURROGATE}),
        ("action", {"type": "give_ap", "target_id": SURROGATE, "amount": 1}),
        ("action", {"type": "give_hp", "target_id": SURROGATE, "amount": 1}),
        ("vote", {"candidate_id": SURROGATE}),
        ("remove", {"player_id": SURROGATE}),
        ("chat", {"to_id": SURROGATE, "text": "hi"}),
    ):
        r = post_raw(client, f"/api/games/{gid}/{path}", body, token)
        assert 400 <= r.status_code < 500, f"{path} {body}: {r.status_code}"
        r.json()  # the error body itself must be encodable


@pytest.mark.parametrize("token", [123, 1.5, True, ["x"], {"a": 1}, "\ud800", SURROGATE])
def test_websocket_closes_4401_on_malformed_token(api, token, caplog):
    client, _, _ = api
    host, _ = host_and_guest(client)
    with client.websocket_connect(f"/ws/{host['game_id']}") as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        msg = ws.receive_json()
        assert msg["type"] == "error" and msg["error"]["code"] == "unauthorized"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4401
    assert not [rec for rec in caplog.records if rec.exc_info]
