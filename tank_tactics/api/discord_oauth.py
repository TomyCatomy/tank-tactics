"""The lobby's "Connect to Discord" button: the host picks a server and channel on Discord, and Discord
creates the game's webhook, so nobody has to copy a webhook URL by hand.

This is Discord's OAuth2 flow with the ``webhook.incoming`` scope. The host's browser
goes to Discord's authorize page, Discord shows a server and channel picker, then
redirects back to ``/api/discord/callback`` with a one-time code. The server swaps the
code for a token response that includes the new webhook, using the app's client secret,
which never leaves the server. The ``state`` parameter ties the redirect to the game
and host that started it: it is random, single-use and expires after ``STATE_TTL``.

The state alone isn't enough: a host could start an attempt and send the Discord link to
someone else, whose channel would then get the host's game updates. So each attempt also
has a browser key, set as an HttpOnly cookie on the browser that started it
(``api/routes/discord.py``), and the callback accepts the state only together with that
key, as RFC 9700 (OAuth 2.0 security best practice) recommends.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
CALLBACK_PATH = "/api/discord/callback"
STATE_TTL = 600.0  # seconds the host has to finish picking a channel
MAX_PENDING = 1000


class DiscordError(Exception):
    """Discord refused the code or answered without a webhook."""


@dataclass(frozen=True)
class Pending:
    game_id: str
    player_id: str
    redirect_uri: str
    browser_key: str
    expires: float


@dataclass(frozen=True)
class Started:
    url: str  # Discord's authorize page
    browser_key: str  # for the cookie of the browser that goes there


class DiscordConnect:
    """``transport`` lets tests stand in for Discord (an ``httpx.MockTransport``)."""

    def __init__(self, client_id: str, client_secret: str, transport: httpx.BaseTransport | None = None):
        self.client_id = client_id
        self._secret = client_secret
        self._transport = transport
        self._pending: dict[str, Pending] = {}
        self._lock = threading.Lock()

    def start(self, game_id: str, player_id: str, origin: str) -> Started:
        """The Discord page to send the host to, and the key their browser must bring back.
        ``origin`` is the site the host is on (Discord only accepts redirect URIs registered
        for the app, so a wrong one fails there)."""
        state, browser_key = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        redirect_uri = origin.rstrip("/") + CALLBACK_PATH
        now = time.monotonic()
        with self._lock:
            self._pending = {k: p for k, p in self._pending.items() if p.expires > now}
            if len(self._pending) >= MAX_PENDING:
                self._pending.pop(next(iter(self._pending)))
            self._pending[state] = Pending(game_id, player_id, redirect_uri, browser_key, now + STATE_TTL)
        query = {
            "client_id": self.client_id, "response_type": "code", "scope": "webhook.incoming",
            "redirect_uri": redirect_uri, "state": state, "prompt": "consent",
        }
        return Started(f"{AUTHORIZE_URL}?{urlencode(query)}", browser_key)

    def take(self, state: str, browser_key: str) -> Pending | None:
        """The connect attempt ``state`` belongs to, once only, or None if it is unknown,
        expired or was started in another browser (``browser_key`` doesn't match)."""
        with self._lock:
            p = self._pending.pop(state, None)
        if p is None or p.expires <= time.monotonic():
            return None
        return p if secrets.compare_digest(p.browser_key, browser_key) else None

    def exchange(self, code: str, redirect_uri: str) -> str:
        """Swap the authorization code for the URL of the webhook Discord created."""
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
        try:
            with httpx.Client(transport=self._transport, timeout=10) as client:
                r = client.post(TOKEN_URL, data=data, auth=(self.client_id, self._secret))
            url = r.json().get("webhook", {}).get("url") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            raise DiscordError(str(e)) from None
        if not isinstance(url, str):
            raise DiscordError(f"token exchange failed with HTTP {r.status_code}")
        return url

    def greet(self, webhook_url: str, text: str) -> None:
        """Post a first message, so the host sees in Discord that it worked. Best effort."""
        try:
            with httpx.Client(transport=self._transport, timeout=10) as client:
                client.post(webhook_url, json={"content": text, "allowed_mentions": {"parse": []}})
        except httpx.HTTPError:
            pass
