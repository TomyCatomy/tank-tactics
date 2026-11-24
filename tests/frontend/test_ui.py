"""Browser tests of the web client: the real page against a real server.

Fixtures (``server``, ``browser``, ``page``) come from ``tests/frontend/conftest.py``.
"""
from __future__ import annotations

import time

import httpx
import pytest


def create_game(server, players=("Host", "Bob"), config=None) -> list[dict]:
    with httpx.Client(base_url=server.url) as c:
        host = c.post("/api/games", json={"name": players[0], "config": config}).json()
        rest = [c.post(f"/api/games/{host['game_id']}/join", json={"name": n}).json() for n in players[1:]]
    return [host, *rest]


def open_as(page, server, player: dict) -> None:
    """Open the game with this player's personal rejoin link."""
    page.goto(f"{server.url}/#/g/{player['game_id']}?t={player['token']}")
    page.wait_for_selector(".game-code")


def visible_text(page, selector: str = "body") -> str:
    return page.locator(selector).inner_text()


# Every optional top-bar badge switched on, and off, so conditional slots are exercised both ways.
ALL_BADGES = {"fog_of_war": "alliance", "jury": {"enabled": True}, "debug": {"fast_ticks": True, "local_seats": True}}


@pytest.mark.parametrize("config", [None, ALL_BADGES], ids=["defaults", "all-badges"])
def test_no_null_or_undefined_text(server, page, config):
    host, bob = create_game(server, config=config)
    open_as(page, server, host)
    page.wait_for_selector("text=Game settings")
    for text in (visible_text(page, "header"), visible_text(page, "#app")):
        assert "null" not in text and "undefined" not in text, text

    with httpx.Client(base_url=server.url) as c:
        assert c.post(f"/api/games/{host['game_id']}/start",
                      headers={"Authorization": f"Bearer {host['token']}"}).status_code == 200
    page.wait_for_selector("svg.board")
    for text in (visible_text(page, "header"), visible_text(page, "#app")):
        assert "null" not in text and "undefined" not in text, text


def test_single_reconnecting_badge_while_server_is_down(server, page):
    host, _ = create_game(server)
    attempts: list[str] = []
    page.on("websocket", lambda ws: attempts.append(ws.url))
    open_as(page, server, host)
    badge = page.locator("#topbar-info .badge", has_text="reconnecting")
    assert badge.count() == 0

    server.stop()
    # Reconnects back off 0.5s, 1s, 2s...; wait until several attempts have failed.
    deadline = time.monotonic() + 15
    while len(attempts) < 4 and time.monotonic() < deadline:
        page.wait_for_timeout(200)
    assert len(attempts) >= 4, f"expected several reconnect attempts, saw {len(attempts)}"
    assert badge.count() == 1, visible_text(page, "header")

    server.start()
    badge.wait_for(state="detached", timeout=20_000)
    assert page.locator("#topbar-info .badge", has_text="reconnecting").count() == 0
    assert "null" not in visible_text(page, "header")


def test_game_code_is_prominent_and_copies(server, page, browser):
    host, _ = create_game(server)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=server.url)
    open_as(page, server, host)
    button = page.locator("button.game-code")
    assert host["game_id"] in button.inner_text()
    button.click()
    assert page.evaluate("navigator.clipboard.readText()") == host["game_id"]


def test_rules_modal_opens_and_closes_from_top_bar(server, page):
    page.goto(server.url)
    modal = page.locator("#rules-modal")
    opener = page.locator("#rules-open")
    assert not modal.is_visible()

    opener.click()
    assert modal.is_visible() and "last tank standing" in modal.inner_text()
    page.locator("#rules-close").click()
    assert not modal.is_visible()
    assert page.evaluate("document.activeElement.id") == "rules-open"

    opener.click()
    page.keyboard.press("Escape")
    assert not modal.is_visible()

    opener.click()
    page.mouse.click(2, 2)  # the backdrop
    assert not modal.is_visible()


def test_rules_modal_survives_state_push_and_fits_phone(server, page):
    host, _ = create_game(server)
    page.set_viewport_size({"width": 320, "height": 568})
    open_as(page, server, host)
    page.locator("#rules-open").click()

    with httpx.Client(base_url=server.url) as c:
        assert c.post(f"/api/games/{host['game_id']}/start",
                      headers={"Authorization": f"Bearer {host['token']}"}).status_code == 200
    page.wait_for_selector("svg.board", state="attached")
    assert page.locator("#rules-modal").is_visible()

    body = page.locator("#rules-body")
    assert body.evaluate("el => el.scrollHeight > el.clientHeight")  # long rules scroll inside the modal
    assert body.evaluate("el => el.scrollWidth <= el.clientWidth")
    box = page.locator("#rules-modal").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 320 and box["y"] + box["height"] <= 568


def test_phone_short_page_does_not_scroll_and_rules_modal_is_tall(server, page):
    host, _ = create_game(server)
    page.set_viewport_size({"width": 390, "height": 844})
    page.route_web_socket("**/ws**", lambda ws: None)  # never connects: stays on the short "Connecting..." screen
    page.goto(f"{server.url}/#/g/{host['game_id']}?t={host['token']}")
    page.wait_for_selector(".spinner")
    assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight")

    page.locator("#rules-open").click()
    box = page.locator("#rules-modal").bounding_box()
    assert 0.65 * 844 <= box["height"] <= 0.75 * 844


def test_lobby_text_follows_minimum_players(server, page):
    host, _ = create_game(server, config={"players": {"minimum": 4}})
    open_as(page, server, host)
    page.wait_for_selector("text=at least 4 players")
    assert "at least 2 players" not in visible_text(page, "#app")


def test_local_seats_add_and_switch_players(server, page):
    host, = create_game(server, players=("Host",), config={"debug": {"local_seats": True}, "players": {"minimum": 3}})
    open_as(page, server, host)
    page.get_by_role("button", name="Fill to minimum (+2)").click()
    page.locator(".plist li", has_text="Player 3").wait_for()
    switcher = page.locator(".seat-switch select")
    switcher.wait_for()
    assert switcher.locator("option").count() == 3

    switcher.select_option(label="Player 2")
    page.locator(".plist li", has_text="Player 2 (you)").wait_for()
    assert "Waiting for the host" in visible_text(page, "#app")  # Player 2 isn't the host


def test_only_actionable_rows_look_clickable(server, page):
    host, _ = create_game(server)
    open_as(page, server, host)
    page.wait_for_selector(".plist li")
    cursors = page.eval_on_selector_all(".plist li", "els => els.map(e => getComputedStyle(e).cursor)")
    assert cursors and all(c != "pointer" for c in cursors), cursors  # lobby rows do nothing on click


def saved_games(page) -> dict:
    return page.evaluate("JSON.parse(localStorage.getItem('tt.games') || '{}')")


def test_player_can_leave_lobby(server, browser):
    host, bob = create_game(server)
    host_page, bob_page = browser.new_page(), browser.new_page()  # separate contexts: separate storage
    try:
        open_as(host_page, server, host)
        open_as(bob_page, server, bob)
        assert host_page.get_by_role("button", name="Leave lobby").count() == 0  # the host closes instead

        bob_page.on("dialog", lambda d: d.accept())
        bob_page.get_by_role("button", name="Leave lobby").click()
        bob_page.wait_for_selector("text=Create a game")  # back on the home page
        assert bob["game_id"] not in saved_games(bob_page)

        host_page.locator(".plist li", has_text="Bob").wait_for(state="detached")
    finally:
        host_page.close()
        bob_page.close()


def test_host_can_close_lobby(server, browser):
    host, bob = create_game(server)
    host_page, bob_page = browser.new_page(), browser.new_page()
    try:
        open_as(host_page, server, host)
        open_as(bob_page, server, bob)
        host_page.on("dialog", lambda d: d.accept())
        host_page.get_by_role("button", name="Close lobby").click()

        for pg in (host_page, bob_page):
            pg.wait_for_selector("text=This lobby was closed")
            assert pg.locator("#topbar-info .badge", has_text="Closed").count() == 1

        # The invite link now says the lobby is closed instead of offering to join.
        fresh = browser.new_page()
        fresh.goto(f"{server.url}/#/join/{host['game_id']}")
        fresh.wait_for_selector("text=This lobby was closed")
        assert fresh.get_by_role("button", name="Join game").count() == 0
        fresh.close()
    finally:
        host_page.close()
        bob_page.close()


def test_dismissing_the_confirm_keeps_you_in(server, page):
    _, bob = create_game(server)
    open_as(page, server, bob)
    page.on("dialog", lambda d: d.dismiss())
    page.get_by_role("button", name="Leave lobby").click()
    page.wait_for_timeout(300)
    assert page.locator(".plist li", has_text="Bob (you)").count() == 1


def test_leaving_as_a_local_seat_switches_to_another_seat(server, page):
    host, = create_game(server, players=("Host",), config={"debug": {"local_seats": True}})
    open_as(page, server, host)
    page.get_by_role("button", name="Add player").click()
    page.locator(".plist li", has_text="Player 2").wait_for()
    page.locator(".seat-switch select").select_option(label="Player 2")
    page.locator(".plist li", has_text="Player 2 (you)").wait_for()

    page.on("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Leave lobby").click()
    # Back in the lobby as the host, not kicked to the home page, and the host seat survives.
    page.locator(".plist li", has_text="Host (you)").wait_for()
    assert page.locator(".plist li", has_text="Player 2").count() == 0
    assert page.locator(".seat-switch").count() == 0  # only one local seat left
    assert saved_games(page)[host["game_id"]]["playerId"] == host["player_id"]


# Measures each <select>'s longest option with the select's own font and checks it fits
# inside the box (minus padding and the dropdown arrow), so labels are never clipped.
_CLIPPED_SELECTS = """() => {
  const ctx = document.createElement('canvas').getContext('2d');
  const clipped = [];
  for (const sel of document.querySelectorAll('select')) {
    if (!sel.offsetParent) continue;  // not rendered
    const cs = getComputedStyle(sel);
    ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const room = sel.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 24;  // 24px: arrow
    for (const opt of sel.options) {
      const need = ctx.measureText(opt.text).width;
      if (need > room) clipped.push(`${opt.text} (${Math.round(need)}px > ${Math.round(room)}px)`);
    }
  }
  return clipped;
}"""


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 900}, {"width": 360, "height": 740}],
                         ids=["desktop", "phone"])
def test_config_dropdowns_show_full_option_text(server, page, viewport):
    page.set_viewport_size(viewport)
    page.goto(server.url)
    page.wait_for_selector("text=Create a game")
    page.locator("details.adv summary").first.click()  # reveal the advanced settings too
    assert page.evaluate(_CLIPPED_SELECTS) == [], "create form"

    host, _ = create_game(server)  # the same form in the lobby's (narrower) settings card
    open_as(page, server, host)
    page.wait_for_selector("text=Game settings")
    page.locator("details.adv summary").first.click()
    assert page.evaluate(_CLIPPED_SELECTS) == [], "lobby settings"


def test_debug_options_hidden_when_not_allowed(server_without_debug, page):
    server = server_without_debug
    page.goto(server.url)
    page.wait_for_selector("text=Create a game")
    assert "Debug" not in visible_text(page, "#app")
    page.fill("input[placeholder='Your display name']", "Host")
    page.click("button:has-text('Create game')")  # the hidden fields must not break creation
    page.wait_for_selector("text=Game settings")
    assert "Debug" not in visible_text(page, "#app")


def test_debug_options_shown_when_allowed(server, page):
    page.goto(server.url)
    page.wait_for_selector("text=Debug: fast ticks")
