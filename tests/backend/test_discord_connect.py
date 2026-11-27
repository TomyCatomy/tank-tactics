"""The lobby's "Connect to Discord" OAuth flow, against a fake Discord."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from tank_tactics.api import create_app
from tank_tactics.api import discord_oauth as discord_mod
from tank_tactics.api.discord_oauth import DiscordConnect
from tank_tactics.api.routes import discord as discord_routes

from ..support import make_service
from .test_api import auth, new_game

ORIGIN = "https://tanktactics.example"
WEBHOOK = "https://discord.com/api/webhooks/42/hook-token"


class FakeDiscord:
    """Answers the token exchange and records webhook posts."""

    def __init__(self, status: int = 200):
        self.status = status
        self.posts: list[dict] = []
        self.exchanges: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url == discord_mod.TOKEN_URL:
            self.exchanges.append({"auth": request.headers["authorization"], **parse_qs(request.content.decode())})
            body = {"access_token": "x", "webhook": {"url": WEBHOOK, "channel_id": "7"}} if self.status == 200 else {}
            return httpx.Response(self.status, json=body)
        self.posts.append({"url": str(request.url), **json.loads(request.content)})
        return httpx.Response(204)


@pytest.fixture
def discord_api():
    """(client, service, fake Discord) with Discord connect turned on."""
    svc, _ = make_service()
    fake = FakeDiscord()
    with TestClient(create_app(svc, discord_connect=DiscordConnect("123", "s3cret", httpx.MockTransport(fake)))) as c:
        yield c, svc, fake


def start(client: TestClient, player: dict) -> httpx.Response:
    return client.post(f"/api/games/{player['game_id']}/discord/connect", json={"origin": ORIGIN}, headers=auth(player))


def authorize(client: TestClient, player: dict) -> dict[str, str]:
    r = start(client, player)
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == discord_mod.AUTHORIZE_URL
    return {k: v[0] for k, v in parse_qs(url.query).items()}


def callback(client: TestClient, **query: str) -> str:
    r = client.get("/api/discord/callback", params=query, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


def webhook_of(svc, game_id: str) -> str | None:
    return svc.get(game_id).config.notifications.discord_webhook_url


def test_connect_saves_the_webhook_discord_created(discord_api):
    client, svc, fake = discord_api
    host = new_game(client, names=("Ann", "Bob"))[0]
    q = authorize(client, host)
    assert q["client_id"] == "123" and q["scope"] == "webhook.incoming" and q["response_type"] == "code"
    assert q["redirect_uri"] == f"{ORIGIN}/api/discord/callback"

    assert callback(client, code="abc", state=q["state"]) == f"/?discord=connected#/g/{host['game_id']}"
    assert webhook_of(svc, host["game_id"]) == WEBHOOK
    (ex,) = fake.exchanges
    assert ex["code"] == ["abc"] and ex["grant_type"] == ["authorization_code"]
    assert ex["redirect_uri"] == [q["redirect_uri"]] and ex["auth"].startswith("Basic ")
    (greeting,) = fake.posts
    assert greeting["url"] == WEBHOOK and host["game_id"] in greeting["content"]
    # The state is single-use.
    assert callback(client, code="abc", state=q["state"]) == "/?discord=expired#/"


def test_cancelled_failed_and_unknown_attempts_change_nothing(discord_api):
    client, svc, fake = discord_api
    host = new_game(client, names=("Ann", "Bob"))[0]
    gid = host["game_id"]
    q = authorize(client, host)
    assert callback(client, error="access_denied", state=q["state"]) == f"/?discord=cancelled#/g/{gid}"
    fake.status = 400
    q = authorize(client, host)
    assert callback(client, code="bad", state=q["state"]) == f"/?discord=failed#/g/{gid}"
    assert callback(client, code="abc", state="made-up") == "/?discord=expired#/"
    assert webhook_of(svc, gid) is None and fake.posts == []


def test_connect_attempts_expire(discord_api, monkeypatch):
    client, svc, _ = discord_api
    host = new_game(client, names=("Ann", "Bob"))[0]
    q = authorize(client, host)
    later = discord_mod.time.monotonic() + discord_mod.STATE_TTL + 1
    monkeypatch.setattr(discord_mod.time, "monotonic", lambda: later)
    assert callback(client, code="abc", state=q["state"]) == "/?discord=expired#/"
    assert webhook_of(svc, host["game_id"]) is None


def test_game_started_before_the_host_came_back(discord_api):
    client, svc, _ = discord_api
    host = new_game(client, names=("Ann", "Bob"))[0]
    q = authorize(client, host)
    assert client.post(f"/api/games/{host['game_id']}/start", headers=auth(host)).status_code == 200
    assert callback(client, code="abc", state=q["state"]) == f"/?discord=failed#/g/{host['game_id']}"
    assert webhook_of(svc, host["game_id"]) is None


def test_only_the_host_of_a_lobby_can_connect(discord_api):
    client, _, _ = discord_api
    host, guest = new_game(client, names=("Ann", "Bob"))
    r = start(client, guest)
    assert r.status_code == 400 and r.json()["error"]["code"] == "forbidden"
    bad = client.post(f"/api/games/{host['game_id']}/discord/connect", json={"origin": "javascript:alert(1)"},
                      headers=auth(host))
    assert bad.status_code == 400
    client.post(f"/api/games/{host['game_id']}/start", headers=auth(host))
    assert start(client, host).json()["error"]["code"] == "not_lobby"


def test_off_without_discord_credentials(api):
    client, _, _ = api
    host = new_game(client, names=("Ann", "Bob"))[0]
    assert client.get("/api/auth/config").json()["discord_connect"] is False
    assert start(client, host).status_code == 404
    assert client.get("/api/discord/callback", params={"state": "x"}, follow_redirects=False).status_code == 404


def test_config_reports_discord_connect(discord_api):
    client, _, _ = discord_api
    assert client.get("/api/auth/config").json()["discord_connect"] is True


def test_the_link_only_works_in_the_browser_that_started_it(discord_api):
    """A host who sends their Discord link to someone else must not get that person's channel."""
    client, svc, fake = discord_api
    host = new_game(client, names=("Ann", "Bob"))[0]
    q = authorize(client, host)
    with TestClient(client.app) as other_browser:  # no cookie from the host's start
        r = other_browser.get("/api/discord/callback", params={"code": "abc", "state": q["state"]},
                              follow_redirects=False)
    assert r.headers["location"] == "/?discord=expired#/"
    assert webhook_of(svc, host["game_id"]) is None and fake.exchanges == []
    # The attempt is used up, so the host's own browser can't finish it either now.
    assert callback(client, code="abc", state=q["state"]) == "/?discord=expired#/"


def test_a_newer_attempt_replaces_the_older_one(discord_api):
    client, svc, _ = discord_api
    host = new_game(client, names=("Ann", "Bob"))[0]
    first, second = authorize(client, host), authorize(client, host)
    assert callback(client, code="abc", state=first["state"]) == "/?discord=expired#/"
    assert webhook_of(svc, host["game_id"]) is None
    # The callback cleared the cookie, so the second attempt needs it set again.
    client.cookies.set(discord_routes.COOKIE, client.app.state.discord._pending[second["state"]].browser_key,
                       path=discord_mod.CALLBACK_PATH)
    assert callback(client, code="abc", state=second["state"]).startswith("/?discord=connected")


@pytest.mark.parametrize("base_url, secure", [("http://testserver", False), ("https://testserver", True)])
def test_browser_key_cookie(discord_api, base_url, secure):
    client, _, _ = discord_api
    host = new_game(client, names=("Ann", "Bob"))[0]
    client.base_url = base_url
    cookie = start(client, host).headers["set-cookie"].lower()
    assert cookie.startswith(f"{discord_routes.COOKIE}=")
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api/discord/callback" in cookie
    assert ("; secure" in cookie) is secure
    cleared = client.get("/api/discord/callback", params={"state": "x"}, follow_redirects=False)
    assert f'{discord_routes.COOKIE}=""' in cleared.headers["set-cookie"]
