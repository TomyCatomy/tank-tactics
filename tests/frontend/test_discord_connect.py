"""The lobby's "Connect to Discord" button, with Discord faked on both sides: the browser's
visit to Discord's authorize page and the server's token exchange."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tank_tactics.api.discord_oauth import DiscordConnect

from ..backend.test_discord_connect import WEBHOOK, FakeDiscord
from ..support import make_service
from .conftest import LiveServer
from .test_ui import create_game, open_as


@pytest.fixture
def discord_server():
    svc, _ = make_service(action_rate_per_second=100)
    fake = FakeDiscord()
    srv = LiveServer(svc, discord_connect=DiscordConnect("123", "s3cret", httpx.MockTransport(fake)))
    srv.start()
    yield srv
    srv.stop()


def fake_discord_consent(page, server, choice: str = "code=abc") -> None:
    """Discord's authorize page, answered at once as if the host picked a channel (or cancelled)."""
    def answer(route):
        state = parse_qs(urlparse(route.request.url).query)["state"][0]
        back = f"{server.url}/api/discord/callback?{choice}&state={state}"
        route.fulfill(status=302, headers={"Location": back})
    page.route("https://discord.com/oauth2/authorize**", answer)


def test_host_connects_and_disconnects_discord(discord_server, page):
    host, guest = create_game(discord_server)
    fake_discord_consent(page, discord_server)
    open_as(page, discord_server, host)
    page.get_by_role("button", name="Connect to Discord").click()

    page.wait_for_selector("text=Connected! Game updates will be posted")
    page.wait_for_selector("text=Connected to Discord: game updates are posted there.")
    assert "discord=" not in page.url and page.url.endswith(f"#/g/{host['game_id']}")
    gid = host["game_id"]
    assert discord_server.service.get(gid).config.notifications.discord_webhook_url == WEBHOOK

    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Disconnect").click()
    page.get_by_role("button", name="Connect to Discord").wait_for()
    assert discord_server.service.get(gid).config.notifications.discord_webhook_url is None


def test_explanation_is_in_a_tooltip(discord_server, page):
    host, _ = create_game(discord_server)
    open_as(page, discord_server, host)
    tip = page.get_by_role("tooltip")
    page.get_by_role("button", name="Connect to Discord").wait_for()
    assert not tip.is_visible()
    page.get_by_role("button", name="More info").click()  # a tap on a phone
    assert tip.is_visible() and "Hidden moves never are" in tip.inner_text()
    page.mouse.move(0, 0)  # still open: it was tapped, not just hovered
    assert tip.is_visible()
    page.keyboard.press("Escape")
    assert not tip.is_visible()


def test_cancelling_on_discord_returns_to_the_lobby(discord_server, page):
    host, _ = create_game(discord_server)
    fake_discord_consent(page, discord_server, choice="error=access_denied")
    open_as(page, discord_server, host)
    page.get_by_role("button", name="Connect to Discord").click()
    page.wait_for_selector("text=Discord connection cancelled.")
    assert page.get_by_role("button", name="Connect to Discord").is_visible()


def test_guests_and_servers_without_discord_see_no_button(discord_server, server, page):
    _, guest = create_game(discord_server)
    open_as(page, discord_server, guest)
    page.wait_for_selector("text=Game settings")
    page.wait_for_load_state("networkidle")  # /api/auth/config, which decides on the button
    assert page.get_by_role("button", name="Connect to Discord").count() == 0
    host, _ = create_game(server)
    open_as(page, server, host)
    page.wait_for_selector("text=Game settings")
    page.wait_for_load_state("networkidle")
    assert page.get_by_role("button", name="Connect to Discord").count() == 0
