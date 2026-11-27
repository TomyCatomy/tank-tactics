"""Resetting a seat's rejoin link: every older token for the seat stops working."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tank_tactics.api import create_app
from tank_tactics.service import ServiceError

from ..support import FAKE_GOOGLE_CLIENT_ID, fake_google_verifier, make_service


@pytest.fixture
def client():
    svc, _ = make_service(action_rate_per_second=100, google_client_id=FAKE_GOOGLE_CLIENT_ID)
    with TestClient(create_app(svc, google_verifier=fake_google_verifier)) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sign_in(client, sub: str) -> str:
    return client.post("/api/auth/google", json={"credential": f"good:{sub}:{sub}"}).json()["token"]


def state(client, gid: str, token: str):
    return client.get(f"/api/games/{gid}/state", headers=bearer(token))


def test_reset_replaces_every_token_for_the_seat(client):
    ann = sign_in(client, "ann")
    host = client.post("/api/games", json={"name": "Ann"}, headers=bearer(ann)).json()
    gid = host["game_id"]
    bob = client.post(f"/api/games/{gid}/join", json={"name": "Bob"}).json()
    claimed = client.post(f"/api/me/games/{gid}/claim", headers=bearer(ann)).json()["token"]

    r = client.post(f"/api/games/{gid}/reset-link", headers=bearer(claimed))
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["game_id"] == gid and new["player_id"] == host["player_id"]
    assert new["token"] not in (host["token"], claimed)
    for old in (host["token"], claimed):
        assert state(client, gid, old).status_code == 401
    assert state(client, gid, new["token"]).json()["viewer_id"] == host["player_id"]
    assert state(client, gid, bob["token"]).status_code == 200  # other seats are untouched
    assert client.post(f"/api/games/{gid}/reset-link").status_code == 401


def test_reset_unlinks_the_seat_until_the_new_token_is_linked(client):
    """A stranger who opened a leaked link while signed in linked the seat to their account."""
    host = client.post("/api/games", json={"name": "Ann"}).json()
    gid = host["game_id"]
    eve = sign_in(client, "eve")
    assert client.post("/api/me/seats/link", json={"tokens": [host["token"]]}, headers=bearer(eve)).json() == {"linked": 1}

    new = client.post(f"/api/games/{gid}/reset-link", headers=bearer(host["token"])).json()
    assert client.get("/api/me/games", headers=bearer(eve)).json() == []
    assert client.post(f"/api/me/games/{gid}/claim", headers=bearer(eve)).status_code == 404
    # The real player, signed in, links the new token back to their own account.
    ann = sign_in(client, "ann")
    assert client.post("/api/me/seats/link", json={"tokens": [new["token"]]}, headers=bearer(ann)).json() == {"linked": 1}


def test_reset_closes_sockets_opened_with_old_tokens(client):
    host = client.post("/api/games", json={"name": "Ann"}).json()
    gid = host["game_id"]
    bob = client.post(f"/api/games/{gid}/join", json={"name": "Bob"}).json()
    with client.websocket_connect(f"/ws/{gid}") as ann_ws, client.websocket_connect(f"/ws/{gid}") as bob_ws:
        ann_ws.send_json({"type": "auth", "token": host["token"]})
        bob_ws.send_json({"type": "auth", "token": bob["token"]})
        assert ann_ws.receive_json()["type"] == "state" and bob_ws.receive_json()["type"] == "state"

        client.post(f"/api/games/{gid}/reset-link", headers=bearer(host["token"]))
        msg = ann_ws.receive_json()
        assert msg["type"] == "error" and msg["error"]["code"] == "link_reset"
        with pytest.raises(WebSocketDisconnect) as closed:
            ann_ws.receive_json()
        assert closed.value.code == 4401
        # Bob's socket stays open and keeps getting pushes.
        client.post(f"/api/games/{gid}/chat", json={"text": "hi"}, headers=bearer(bob["token"]))
        assert bob_ws.receive_json()["type"] == "state"


def test_reset_works_after_the_game_and_survives_a_restart(db):
    svc, clock = make_service(db)
    host = svc.create_game("Ann")
    gid = host["game_id"]
    bob = svc.join_game(gid, "Bob")
    svc.start_game(gid, host["player_id"])
    svc.remove_player(gid, host["player_id"], bob["player_id"])  # game over: Ann wins
    assert svc.get(gid).status == "finished"

    new = svc.reset_seat_token(gid, bob["player_id"])
    svc2, _ = make_service(db, clock=clock)
    assert svc2.authenticate(gid, new["token"]) == bob["player_id"]
    with pytest.raises(ServiceError):
        svc2.authenticate(gid, bob["token"])
