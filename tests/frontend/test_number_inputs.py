"""The settings form: the defaults a new game starts with."""
from __future__ import annotations

from .test_ui import open_create_form


def open_settings(page, server):
    page.goto(server.url)
    open_create_form(page)
    page.locator("details.adv summary").first.click()  # "Advanced rules"


def test_create_form_shows_the_new_defaults(server, page):
    open_settings(page, server)
    assert page.get_by_label("Starting AP").input_value() == "0"
    assert page.get_by_label("Jury of dead players (vote for +1 AP)").is_checked()
    assert not page.get_by_label("Killer loots victim’s AP").is_checked()
