"""Optional Google sign-in: token verification, sessions, linked seats, and the
schema upgrade for databases made before accounts existed."""
from __future__ import annotations

import sqlite3
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from tank_tactics.api import create_app
from tank_tactics.api.google import GoogleIdentity, GoogleTokenVerifier, InvalidGoogleToken
from tank_tactics.service import ServiceError

from ..support import FAKE_GOOGLE_CLIENT_ID as CLIENT_ID
from ..support import fake_google_verifier as fake_verifier
from ..support import make_service


@pytest.fixture
def acct():
    """(client, service, clock) with Google sign-in on and a fake token verifier."""
    svc, clock = make_service(action_rate_per_second=100, google_client_id=CLIENT_ID)
    with TestClient(create_app(svc, google_verifier=fake_verifier)) as client:
        yield client, svc, clock


def sign_in(client, sub="sub-ann", name="Ann") -> str:
    r = client.post("/api/auth/google", json={"credential": f"good:{sub}:{name}"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- the real verifier ------------------------------------------------------------------------

class _Keys:
    """Stands in for PyJWKClient: always returns our test public key."""

    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token):
        return type("K", (), {"key": self.public_key})()


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _id_token(key, **overrides) -> str:
    now = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "1234", "iat": now,
              "exp": now + 3600, "given_name": "Ann", "name": "Ann Example", "email": "ann@example.com"}
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def test_verifier_accepts_a_valid_google_token(rsa_key):
    verify = GoogleTokenVerifier(CLIENT_ID, jwks_client=_Keys(rsa_key.public_key()))
    assert verify(_id_token(rsa_key)) == GoogleIdentity(sub="1234", name="Ann")


@pytest.mark.parametrize("overrides", [
    {"aud": "someone-elses-app"},
    {"iss": "https://evil.example.com"},
    {"exp": int(time.time()) - 3600},
    {"sub": None},
], ids=["audience", "issuer", "expired", "no-subject"])
def test_verifier_rejects_bad_claims(rsa_key, overrides):
    verify = GoogleTokenVerifier(CLIENT_ID, jwks_client=_Keys(rsa_key.public_key()))
    with pytest.raises(InvalidGoogleToken):
        verify(_id_token(rsa_key, **overrides))


def test_verifier_rejects_a_forged_signature(rsa_key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verify = GoogleTokenVerifier(CLIENT_ID, jwks_client=_Keys(rsa_key.public_key()))
    with pytest.raises(InvalidGoogleToken):
        verify(_id_token(other))
    with pytest.raises(InvalidGoogleToken):
        verify("not a jwt")


# -- API ------------------------------------------------------------------------------------

def test_sign_in_is_off_without_a_client_id(api):
    client, _, _ = api
    assert client.get("/api/auth/config").json() == {"google_client_id": None}
    r = client.post("/api/auth/google", json={"credential": "good:x:y"})
    assert r.status_code == 404


def test_sign_in_and_me(acct):
    client, _, _ = acct
    assert client.get("/api/auth/config").json() == {"google_client_id": CLIENT_ID}
    token = sign_in(client)
    me = client.get("/api/me", headers=bearer(token)).json()
    assert me["name"] == "Ann"
    # Same Google account again: same user, profile name updated.
    token2 = sign_in(client, name="Annie")
    assert client.get("/api/me", headers=bearer(token2)).json() == {"id": me["id"], "name": "Annie"}


def test_bad_google_token_is_rejected(acct):
    client, _, _ = acct
    r = client.post("/api/auth/google", json={"credential": "forged"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_me_needs_a_live_session(acct):
    client, _, clock = acct
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=bearer("nope")).status_code == 401
    token = sign_in(client)
    clock.t += 31 * 86_400  # past the default 30-day session
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_logout_ends_the_session_but_not_seat_tokens(acct):
    client, _, _ = acct
    token = sign_in(client)
    host = client.post("/api/games", json={"name": "Ann"}, headers=bearer(token)).json()
    assert client.post("/api/auth/logout", headers=bearer(token)).status_code == 200
    assert client.get("/api/me", headers=bearer(token)).status_code == 401
    assert client.get(f"/api/games/{host['game_id']}/state", headers=bearer(host["token"])).status_code == 200


def test_seats_are_linked_and_claimable_on_another_device(acct):
    client, _, _ = acct
    ann = sign_in(client, "sub-ann", "Ann")
    bob = sign_in(client, "sub-bob", "Bob")
    host = client.post("/api/games", json={"name": "Ann"}, headers=bearer(ann)).json()
    gid = host["game_id"]
    joined = client.post(f"/api/games/{gid}/join", json={"name": "Bob"}, headers=bearer(bob)).json()
    client.post(f"/api/games/{gid}/join", json={"name": "Anon"})  # not signed in: not linked to anyone

    games = client.get("/api/me/games", headers=bearer(bob)).json()
    assert games == [{"game_id": gid, "player_id": joined["player_id"], "name": "Bob", "status": "lobby",
                      "is_host": False}]
    assert client.get("/api/me/games", headers=bearer(ann)).json()[0]["is_host"] is True

    # "Another device": a fresh seat token for the same player; the old one still works.
    claimed = client.post(f"/api/me/games/{gid}/claim", headers=bearer(bob)).json()
    assert claimed["player_id"] == joined["player_id"] and claimed["token"] != joined["token"]
    for t in (claimed["token"], joined["token"]):
        state = client.get(f"/api/games/{gid}/state", headers=bearer(t)).json()
        assert state["viewer_id"] == joined["player_id"]


def test_claim_needs_a_seat_in_that_game(acct):
    client, _, _ = acct
    ann = sign_in(client, "sub-ann", "Ann")
    bob = sign_in(client, "sub-bob", "Bob")
    gid = client.post("/api/games", json={"name": "Ann"}, headers=bearer(ann)).json()["game_id"]
    r = client.post(f"/api/me/games/{gid}/claim", headers=bearer(bob))
    assert r.status_code == 404
    assert client.post(f"/api/me/games/{gid}/claim").status_code == 401


def test_one_seat_per_account_per_game(acct):
    client, _, _ = acct
    ann = sign_in(client)
    gid = client.post("/api/games", json={"name": "Ann"}, headers=bearer(ann)).json()["game_id"]
    r = client.post(f"/api/games/{gid}/join", json={"name": "Ann again"}, headers=bearer(ann))
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_joined"
    # Joining without the session (e.g. debug local seats) is still allowed.
    assert client.post(f"/api/games/{gid}/join", json={"name": "Seat 2"}).status_code == 201


def test_create_with_an_expired_session_is_refused(acct):
    """Rather than silently creating an unlinked seat."""
    client, _, clock = acct
    token = sign_in(client)
    clock.t += 31 * 86_400
    r = client.post("/api/games", json={"name": "Ann"}, headers=bearer(token))
    assert r.status_code == 401


def test_claimed_tokens_die_with_the_seat(acct):
    client, svc, _ = acct
    ann = sign_in(client, "sub-ann", "Ann")
    bob = sign_in(client, "sub-bob", "Bob")
    host = client.post("/api/games", json={"name": "Ann"}, headers=bearer(ann)).json()
    gid = host["game_id"]
    client.post(f"/api/games/{gid}/join", json={"name": "Bob"}, headers=bearer(bob))
    claimed = client.post(f"/api/me/games/{gid}/claim", headers=bearer(bob)).json()
    client.post(f"/api/games/{gid}/leave", headers=bearer(claimed["token"]))
    assert client.get(f"/api/games/{gid}/state", headers=bearer(claimed["token"])).status_code == 401
    assert client.get("/api/me/games", headers=bearer(bob)).json() == []
    # And on disk: the extra token rows went with the player row.
    assert all(g != gid or p != claimed["player_id"] for g, p in svc.store.token_index().values())


def test_accounts_never_leak_into_game_views(acct):
    client, _, _ = acct
    ann = sign_in(client, "sub-ann", "Ann Secret-Surname")
    host = client.post("/api/games", json={"name": "Ann"}, headers=bearer(ann)).json()
    text = client.get(f"/api/games/{host['game_id']}/state", headers=bearer(host["token"])).text
    assert "user_id" not in text and "sub-ann" not in text and "Secret-Surname" not in text


def test_sign_in_is_rate_limited(acct):
    client, _, _ = acct
    codes = [client.post("/api/auth/google", json={"credential": "bad"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


# -- service / persistence ------------------------------------------------------------------------

def test_accounts_and_claimed_tokens_survive_a_restart(tmp_path):
    db = str(tmp_path / "tt.db")
    svc, clock = make_service(db)
    session = svc.sign_in("sub-ann", "Ann")
    uid = session["user"]["id"]
    host = svc.create_game("Ann", user_id=uid)
    claimed = svc.claim_seat(host["game_id"], uid)

    svc2, _ = make_service(db, clock=clock)
    assert svc2.session_user(session["token"]).id == uid
    assert svc2.authenticate(host["game_id"], claimed["token"]) == host["player_id"]
    assert [g["game_id"] for g in svc2.user_games(uid)] == [host["game_id"]]


def test_old_database_gets_the_user_id_column(tmp_path):
    """A database from before accounts: ``players`` has no ``user_id``."""
    db = tmp_path / "old.db"
    svc, clock = make_service(str(db))
    host = svc.create_game("Ann")
    svc.store.close()
    # Rebuild ``players`` as the previous version created it (SQLite can't drop a
    # column that has a foreign key).
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=OFF")
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE name = 'players'").fetchone()[0]
    old = ddl.replace("user_id VARCHAR, ", "").replace(", \n\tFOREIGN KEY(user_id) REFERENCES users (id)", "")
    assert "user_id" not in old
    cols = ", ".join(r[1] for r in con.execute("PRAGMA table_info(players)") if r[1] != "user_id")
    con.execute(old.replace("CREATE TABLE players", "CREATE TABLE players_old"))
    con.execute(f"INSERT INTO players_old ({cols}) SELECT {cols} FROM players")
    con.execute("DROP TABLE players")
    con.execute("ALTER TABLE players_old RENAME TO players")
    con.commit()
    con.close()

    svc2, _ = make_service(str(db), clock=clock)
    assert svc2.authenticate(host["game_id"], host["token"]) == host["player_id"]
    uid = svc2.sign_in("sub-ann", "Ann")["user"]["id"]
    svc2.join_game(host["game_id"], "Ann's account", user_id=uid)
    assert len(svc2.user_games(uid)) == 1
    with pytest.raises(ServiceError):
        svc2.join_game(host["game_id"], "Again", user_id=uid)
