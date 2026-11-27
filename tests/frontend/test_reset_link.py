"""Resetting the personal rejoin link from the lobby and from the Me tab."""
from __future__ import annotations

import httpx

from .test_ui import create_game, open_as


def rejoin_value(page) -> str:
    return page.locator(".rejoin input").first.input_value()


def reset(page) -> None:
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Reset link").click()
    page.wait_for_selector(".toast >> text=Link reset")


def test_reset_in_the_lobby_locks_out_other_devices(server, browser, page):
    host, _ = create_game(server)
    other = browser.new_context().new_page()  # another device with the same link
    open_as(page, server, host)
    open_as(other, server, host)
    old = rejoin_value(page)
    assert host["token"] in old

    reset(page)
    new = rejoin_value(page)
    assert new != old and host["token"] not in new
    other.wait_for_selector("text=Your rejoin link was reset")

    # This device keeps playing with the new token, also after a reload.
    page.reload()
    page.wait_for_selector(".game-code")
    assert rejoin_value(page) == new
    # And the old link opened fresh is refused.
    fresh = browser.new_context().new_page()
    fresh.goto(old)
    fresh.wait_for_selector("text=Invalid player token")
    other.context.close()
    fresh.context.close()


def test_reset_from_the_me_tab_mid_game(server, page):
    host, bob = create_game(server)
    with httpx.Client(base_url=server.url) as c:
        c.post(f"/api/games/{host['game_id']}/start", headers={"Authorization": f"Bearer {host['token']}"})
    open_as(page, server, bob)
    page.get_by_role("button", name="Your rejoin link").click()
    old = rejoin_value(page)
    reset(page)
    assert rejoin_value(page) != old  # the open sheet shows the new link
    with httpx.Client(base_url=server.url) as c:
        assert c.get(f"/api/games/{host['game_id']}/state",
                     headers={"Authorization": f"Bearer {bob['token']}"}).status_code == 401
    # Still connected: the board keeps updating after the socket was replaced.
    page.locator(".sheet-backdrop button", has_text="Close").click()
    with httpx.Client(base_url=server.url) as c:
        c.post(f"/api/games/{host['game_id']}/chat", json={"text": "still here?"},
               headers={"Authorization": f"Bearer {host['token']}"})
    page.get_by_role("tab", name="Chat").click()
    page.wait_for_selector("text=still here?")
