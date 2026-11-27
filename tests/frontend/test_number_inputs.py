"""Number fields: the lobby defaults, the time limit in days, and that no field takes a negative."""
from __future__ import annotations

import httpx

from .test_ui import create_game, open_as, open_create_form

DAY = 86_400
NUMBER_INPUTS = "input[type=number]"


def open_settings(page, server):
    page.goto(server.url)
    open_create_form(page)
    page.locator("details.adv summary").first.click()  # "Advanced rules"


def number_values(page) -> list[str]:
    return page.eval_on_selector_all(NUMBER_INPUTS, "els => els.map(e => e.value)")


def test_create_form_shows_the_new_defaults(server, page):
    open_settings(page, server)
    assert page.get_by_label("Starting AP").input_value() == "0"
    assert page.get_by_label("Jury of dead players (vote for +1 AP)").is_checked()
    assert not page.get_by_label("Killer loots victim’s AP").is_checked()


def test_no_settings_field_can_hold_a_negative(server, page):
    open_settings(page, server)
    fields = page.locator(f"{NUMBER_INPUTS}:visible")
    assert fields.count() >= 10
    for i in range(fields.count()):
        field = fields.nth(i)
        field.fill("")
        field.press_sequentially("-5")  # typing: the minus sign never gets in
        assert field.input_value() == "5", field.get_attribute("aria-label")
        field.fill("-7")  # pasting or autofill: the sign is dropped
        assert field.input_value() == "7"
        field.fill("2.5")
        assert field.input_value() == "2"
    # Fields that are currently hidden (other schedule builders) get the same treatment.
    values = page.eval_on_selector_all(NUMBER_INPUTS, """els => els.map(e => {
        e.value = '-3'; e.dispatchEvent(new Event('input', { bubbles: true })); return e.value; })""")
    assert values and all(v == "3" for v in values), values
    assert not any(v.startswith("-") for v in number_values(page))


def test_time_limit_is_entered_in_days(server, page):
    open_settings(page, server)
    limit = page.get_by_label("Time limit (days)")
    assert limit.input_value() == "" and limit.get_attribute("placeholder") == "None"
    limit.fill("3")
    page.fill("input[placeholder='Your display name']", "Host")
    page.click("button:has-text('Create game')")
    page.wait_for_selector("text=Game settings")
    gid = page.evaluate("location.hash").split("/")[2].split("?")[0]
    assert server.service.get(gid).config.max_duration_seconds == 3 * DAY
    page.locator("details.adv summary").first.click()
    assert page.get_by_label("Time limit (days)").input_value() == "3"  # and it reads back in days


def test_board_multiplier_is_not_a_lobby_setting(server, page):
    open_settings(page, server)
    assert page.get_by_label("Auto board: minimum size").is_visible()
    assert "multiplier" not in page.locator("#app").inner_text()


def test_gift_amounts_cannot_be_negative(server, page):
    host, bob = create_game(server, config={"start": {"ap": 2}})
    with httpx.Client(base_url=server.url) as c:
        r = c.post(f"/api/games/{host['game_id']}/start", headers={"Authorization": f"Bearer {host['token']}"})
        assert r.status_code == 200, r.text
    open_as(page, server, host)
    page.get_by_text("Players", exact=True).click()
    page.locator(".plist li", has_text="Bob").click()
    for what in ("AP", "HP"):
        amount = page.get_by_label(f"{what} amount")
        amount.fill("")
        amount.press_sequentially("-2")
        assert amount.input_value() == "2"
        amount.fill("-4")
        assert amount.input_value() == "4"
