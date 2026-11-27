"""Joining from an invite link by signing in with Google instead of typing a name."""
from __future__ import annotations

from .test_ui import create_game, fake_google


def players(server, gid) -> list[str]:
    return [p["name"] for p in server.service.view(gid, None)["players"]]


def test_invite_page_has_the_same_google_pill_as_the_home_page(auth_server, page):
    fake_google(page)
    host = create_game(auth_server, players=("Host",))[0]
    page.goto(auth_server.url)
    page.wait_for_selector(".fake-gsi")
    home = page.evaluate("window.__gsiButtonOptions")
    page.goto(f"{auth_server.url}/#/join/{host['game_id']}")
    page.reload()  # a fresh page, so the options come from the join page's own call
    page.wait_for_selector(".join-google .fake-gsi")
    assert page.evaluate("window.__gsiButtonOptions") == home
    assert home["shape"] == "pill"
    box, button = page.locator(".join-google").bounding_box(), page.locator(".fake-gsi").bounding_box()
    assert abs((button["x"] + button["width"] / 2) - (box["x"] + box["width"] / 2)) <= 1


def test_google_on_the_invite_page_joins_under_the_google_name(auth_server, page):
    fake_google(page, ("sub-bob", "Bob Builder"))
    host = create_game(auth_server, players=("Host",))[0]
    page.goto(f"{auth_server.url}/#/join/{host['game_id']}")
    page.click(".join-google .fake-gsi")
    page.wait_for_selector(".game-code")
    assert players(auth_server, host["game_id"]) == ["Host", "Bob Builder"]
    # The seat is linked to the account, so it follows Bob to other devices.
    page.goto(auth_server.url)
    page.wait_for_selector(f".plist li:has-text('{host['game_id']}')")


def test_google_on_the_invite_page_reopens_an_existing_seat(auth_server, browser):
    host = create_game(auth_server, players=("Host",))[0]
    url = f"{auth_server.url}/#/join/{host['game_id']}"
    for _ in range(2):  # two devices, both signed out at first
        ctx = browser.new_context()
        page = ctx.new_page()
        fake_google(page, ("sub-bob", "Bob"))
        page.goto(url)
        page.click(".join-google .fake-gsi")
        page.wait_for_selector(".game-code")
        ctx.close()
    assert players(auth_server, host["game_id"]) == ["Host", "Bob"]


def test_google_name_already_taken_leaves_the_name_to_change(auth_server, page):
    fake_google(page, ("sub-bob", "Host"))
    host = create_game(auth_server, players=("Host",))[0]
    page.goto(f"{auth_server.url}/#/join/{host['game_id']}")
    page.click(".join-google .fake-gsi")
    page.wait_for_selector(".toast.error")
    assert page.locator(".join-google").count() == 0  # signed in now: no second button
    name = page.locator("input[autocomplete=nickname]")
    assert name.input_value() == "Host"
    name.fill("Bobby")
    page.click("text=Join game")
    page.wait_for_selector(".game-code")
    assert players(auth_server, host["game_id"]) == ["Host", "Bobby"]


def test_no_google_option_on_the_invite_page_without_a_client_id(server, page):
    host = create_game(server, players=("Host",))[0]
    page.goto(f"{server.url}/#/join/{host['game_id']}")
    page.wait_for_selector("text=Join game")
    assert page.locator(".join-google").count() == 0
