"""The game page while state keeps arriving: AP ticks must not disturb what the player is doing,
open action sheets follow the new state, and right-to-left (Hebrew) text reads correctly."""
from __future__ import annotations

import time

import httpx
import pytest

from ..support import make_service
from .conftest import LiveServer
from .test_ui import create_game, open_as, phone_page


@pytest.fixture
def server():
    """Like the shared ``server``, but on the real clock, so the scheduler really sends AP ticks."""
    svc, _ = make_service(clock=time.time, action_rate_per_second=100)
    srv = LiveServer(svc)
    srv.start()
    yield srv
    if srv.running:
        srv.stop()

# AP every second, so a test sees several ticks quickly.
FAST = {"debug": {"fast_ticks": True, "tick_seconds": 1}}


def start(server, host) -> None:
    with httpx.Client(base_url=server.url) as c:
        r = c.post(f"/api/games/{host['game_id']}/start", headers={"Authorization": f"Bearer {host['token']}"})
        assert r.status_code == 200, r.text


def wait_for_ticks(server, player, ticks: int) -> None:
    """Wait until `player` has received `ticks` AP ticks (each one a state push to every client)."""
    with httpx.Client(base_url=server.url) as c:
        for _ in range(100):
            st = c.get(f"/api/games/{player['game_id']}/state", headers={"Authorization": f"Bearer {player['token']}"}).json()
            if next(p["ap"] for p in st["players"] if p["id"] == st["viewer_id"]) >= ticks:
                return
            time.sleep(0.2)
    raise AssertionError("no AP ticks arrived")


def test_phone_chat_keeps_focus_through_ap_ticks(server, browser):
    host, _ = create_game(server, config=FAST)
    start(server, host)
    page = phone_page(browser, 390, 664)
    try:
        open_as(page, server, host)
        page.get_by_role("tab", name="Chat").click()
        box = page.locator(".chat textarea")
        box.click()
        box.type("half a messa")
        wait_for_ticks(server, host, 2)
        page.wait_for_timeout(300)  # let the last push render
        assert box.evaluate("el => el === document.activeElement"), "the message box lost focus"
        assert box.input_value() == "half a messa"
    finally:
        page.context.close()
