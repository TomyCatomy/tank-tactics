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


def test_phone_short_page_does_not_scroll(server, page):
    host, _ = create_game(server)
    page.set_viewport_size({"width": 390, "height": 844})
    page.route_web_socket("**/ws**", lambda ws: None)  # never connects: stays on the short "Connecting..." screen
    page.goto(f"{server.url}/#/g/{host['game_id']}?t={host['token']}")
    page.wait_for_selector(".spinner")
    assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight")


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
    if viewport["width"] < 900:
        page.get_by_role("tab", name="Settings").click()  # phones show the lobby one tab at a time
    page.wait_for_selector("text=Game settings")
    page.locator("details.adv summary").first.click()
    assert page.evaluate(_CLIPPED_SELECTS) == [], "lobby settings"


# -- Google sign-in --------------------------------------------------------------------------

# Replaces Google's script: renders a button that "signs in" as whoever window.fakeGoogleUser
# names, with a credential the server's fake verifier accepts.
FAKE_GSI = """
window.google = { accounts: { id: {
  initialize(cfg) { window.__gsiCallback = cfg.callback; },
  renderButton(el) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'fake-gsi'; b.textContent = 'Sign in with Google';
    b.onclick = () => { const u = window.fakeGoogleUser || {sub: 'sub-ann', name: 'Ann'};
                        window.__gsiCallback({ credential: `good:${u.sub}:${u.name}` }); };
    el.append(b);
  },
  disableAutoSelect() {},
} } };
"""


def fake_google(page, user=("sub-ann", "Ann")) -> None:
    page.route("https://accounts.google.com/gsi/client",
               lambda route: route.fulfill(status=200, content_type="text/javascript", body=FAKE_GSI))
    page.add_init_script(f"window.fakeGoogleUser = {{sub: {user[0]!r}, name: {user[1]!r}}};")


def test_no_sign_in_without_a_google_client_id(server, page):
    page.goto(server.url)
    page.wait_for_selector("text=Create a game")
    assert page.locator(".account").count() == 0


def test_signed_in_games_follow_you_to_another_device(auth_server, browser):
    # Device 1: sign in and create a game.
    ctx1 = browser.new_context()
    page = ctx1.new_page()
    fake_google(page)
    page.goto(auth_server.url)
    page.click(".fake-gsi")
    page.wait_for_selector("text=Signed in as")
    assert page.locator(".home input[autocomplete=nickname]").input_value() == "Ann"  # name prefilled
    page.click("text=Create game")
    page.wait_for_selector(".game-code")
    gid = page.locator(".game-code-id").inner_text().strip()
    ctx1.close()

    # Device 2: nothing saved locally; signing in lists the game and opens it as Ann.
    ctx2 = browser.new_context()
    page = ctx2.new_page()
    fake_google(page)
    page.goto(auth_server.url)
    page.wait_for_selector(".fake-gsi")
    assert gid not in visible_text(page, "#app")
    page.click(".fake-gsi")
    page.wait_for_selector(f".plist li:has-text('{gid}')")
    page.click(f".plist li:has-text('{gid}')")
    page.wait_for_selector(".game-code")
    st = auth_server.service.view(gid, None)
    assert [p["name"] for p in st["players"]] == ["Ann"]  # reopened the seat, didn't join again

    # Signing out keeps the seat saved on this device.
    page.goto(auth_server.url)
    page.click("text=Sign out")
    page.wait_for_selector(".fake-gsi")
    assert gid in visible_text(page, "#app")
    ctx2.close()


def test_invite_link_opens_your_existing_seat_when_signed_in(auth_server, browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    fake_google(page, ("sub-bob", "Bob"))
    page.goto(auth_server.url)
    page.click(".fake-gsi")
    page.wait_for_selector("text=Signed in as")
    host = create_game(auth_server, players=("Host",))[0]
    page.goto(f"{auth_server.url}/#/join/{host['game_id']}")
    page.click("text=Join game")
    page.wait_for_selector(".game-code")
    # Forget the device's seat, then follow the invite link again: no second seat.
    page.evaluate("localStorage.removeItem('tt.games')")
    page.goto(f"{auth_server.url}/#/")
    page.goto(f"{auth_server.url}/#/join/{host['game_id']}")
    page.wait_for_selector(".game-code")
    names = [p["name"] for p in auth_server.service.view(host["game_id"], None)["players"]]
    assert names == ["Host", "Bob"]
    ctx.close()


PHONES = [(320, 568), (360, 640), (390, 664), (430, 740), (667, 375), (932, 430)]
_PHONE_IDS = [f"{w}x{h}" for w, h in PHONES]


def phone_page(browser, width, height):
    return browser.new_context(viewport={"width": width, "height": height}, is_mobile=True, has_touch=True).new_page()


@pytest.mark.parametrize(("width", "height"), PHONES, ids=_PHONE_IDS)
def test_rules_modal_takes_most_of_phone_screen(server, browser, width, height):
    page = phone_page(browser, width, height)
    try:
        page.goto(server.url)
        page.locator("#rules-open").click()
        box = page.locator("#rules-modal").bounding_box()
        assert (box["x"], box["width"]) == (6, width - 12)
        assert abs(box["height"] - 0.7 * height) <= 1 and abs(box["y"] - 0.15 * height) <= 1  # 70% tall, centred
        body = page.locator("#rules-body")
        assert body.evaluate("el => el.scrollWidth <= el.clientWidth")
        body.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        last = page.locator("#rules-body > p").last.bounding_box()
        assert last["y"] + last["height"] <= height  # the last line can be scrolled fully into view
        page.locator("#rules-close").click()
        assert not page.locator("#rules-modal").is_visible()
    finally:
        page.context.close()


@pytest.mark.parametrize("role", ["host", "guest"])
@pytest.mark.parametrize(("width", "height"), PHONES, ids=_PHONE_IDS)
def test_phone_lobby_needs_no_page_scroll(server, browser, width, height, role):
    host, bob = create_game(server)
    page = phone_page(browser, width, height)
    try:
        open_as(page, server, host if role == "host" else bob)
        page.wait_for_selector(".lobby-overview.active")
        for tab in ("Players", "Chat", "Settings", "Lobby"):
            page.get_by_role("tab", name=tab).click()
            size = page.evaluate("({h: document.documentElement.scrollHeight, w: document.documentElement.scrollWidth})")
            assert size == {"h": height, "w": width}, tab
        overview = page.locator(".lobby-overview")
        assert overview.evaluate("el => el.scrollHeight <= el.clientHeight"), "the first tab must fit without scrolling"
        action = page.get_by_role("button", name="Start game" if role == "host" else "Leave lobby")
        box = action.bounding_box()
        assert box["y"] + box["height"] <= height
    finally:
        page.context.close()


@pytest.mark.parametrize(("width", "height"), PHONES, ids=_PHONE_IDS)
def test_phone_home_scrolls_to_every_control(server, browser, width, height):
    """The home page scrolls like a normal page on phones; nothing is hidden behind a pinned button."""
    page = phone_page(browser, width, height)
    try:
        page.goto(server.url)
        page.wait_for_selector(".home-create")
        assert page.evaluate("document.documentElement.scrollWidth") == width
        assert page.evaluate("getComputedStyle(document.documentElement).overflowY") != "hidden"
        summary = page.locator("details.adv summary").first
        summary.scroll_into_view_if_needed()
        box = summary.bounding_box()
        assert box["y"] >= 0 and box["y"] + box["height"] <= height
        summary.click()
        page.get_by_placeholder("Your display name").fill("Ann")
        create = page.get_by_role("button", name="Create game")
        create.scroll_into_view_if_needed()
        create.click()
        page.wait_for_selector(".game-code")
    finally:
        page.context.close()


@pytest.mark.parametrize("signed_in", [False, True], ids=["signed-out", "signed-in"])
@pytest.mark.parametrize(("width", "height"), PHONES, ids=_PHONE_IDS)
def test_phone_home_with_google_sign_in_reaches_every_control(auth_server, browser, width, height, signed_in):
    # The deployed server always has sign-in on, which adds the account card to the home page.
    page = phone_page(browser, width, height)
    try:
        fake_google(page)
        page.goto(auth_server.url)
        page.wait_for_selector(".fake-gsi")
        if signed_in:
            page.click(".fake-gsi")
            page.wait_for_selector("text=Signed in as")
        assert page.evaluate("document.documentElement.scrollWidth") == width
        for target in (page.get_by_placeholder("Game code, e.g. k7mq2x"), page.locator("details.adv summary").first,
                       page.get_by_role("button", name="Create game")):
            target.scroll_into_view_if_needed()
            box = target.bounding_box()
            assert box["y"] >= 0 and box["y"] + box["height"] <= height
    finally:
        page.context.close()


def test_phone_lobby_chat_tab_counts_unread(server, browser):
    host, bob = create_game(server)
    page = phone_page(browser, 390, 664)
    try:
        open_as(page, server, host)
        page.wait_for_selector(".lobby-overview.active")
        with httpx.Client(base_url=server.url) as c:
            assert c.post(f"/api/games/{host['game_id']}/chat", json={"to_id": None, "text": "hello"},
                          headers={"Authorization": f"Bearer {bob['token']}"}).status_code == 201
        badge = page.locator(".lobby-tabs .count")
        badge.wait_for()
        assert badge.inner_text() == "1"
        page.get_by_role("tab", name="Chat").click()
        assert page.locator(".chat-msgs", has_text="hello").is_visible()
        assert badge.count() == 0
    finally:
        page.context.close()


def test_desktop_lobby_shows_all_panels_without_tabs(server, page):
    host, _ = create_game(server)
    page.set_viewport_size({"width": 1280, "height": 900})
    open_as(page, server, host)
    page.wait_for_selector("text=Game settings")
    assert not page.locator(".lobby-tabs").is_visible()
    for panel in (".lobby-overview", ".lobby-players", ".lobby-chat", ".lobby-settings"):
        assert page.locator(panel).is_visible(), panel


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


@pytest.mark.parametrize(("width", "height"), [(320, 568), (667, 375)], ids=["portrait", "landscape"])
def test_phone_lobby_never_clips_with_large_system_font(server, browser, width, height):
    """Real phones can lay out taller than the emulator (accessibility font size, toolbars): scroll, don't clip."""
    host, _ = create_game(server)
    page = phone_page(browser, width, height)
    try:
        open_as(page, server, host)
        page.wait_for_selector(".lobby-overview.active")
        page.add_style_tag(content="body { font-size: 30px; }")
        assert page.locator(".lobby-overview").evaluate("el => el.clientHeight") >= 100
        start = page.get_by_role("button", name="Start game")
        start.scroll_into_view_if_needed()
        box = start.bounding_box()
        assert box["y"] >= 0 and box["y"] + box["height"] <= height
    finally:
        page.context.close()


def test_phone_text_fields_are_large_enough_to_avoid_ios_zoom(server, browser):
    host, _ = create_game(server)
    page = phone_page(browser, 390, 664)
    try:
        open_as(page, server, host)
        page.wait_for_selector(".lobby-overview.active")
        sizes = page.evaluate("""() => [...document.querySelectorAll('input:not([type=checkbox]), select, textarea')]
            .map(el => parseFloat(getComputedStyle(el).fontSize))""")
        assert sizes and min(sizes) >= 16
    finally:
        page.context.close()
