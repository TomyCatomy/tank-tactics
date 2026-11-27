"""The Content-Security-Policy is really enforced by the browser (``api/security.py``).

Every other browser test already fails on a policy violation (see ``conftest.py``); these
check that the policy blocks what it should.
"""
from __future__ import annotations

import pytest

from .conftest import _violations


def test_injected_markup_cannot_run_script(server, page):
    page.goto(server.url)
    page.wait_for_selector("#app >> text=Tank Tactics", state="attached")
    blocked = page.evaluate("""() => {
        try { document.body.innerHTML += '<img src=x onerror="window.__pwned = 1">'; return false; }
        catch (e) { return e instanceof TypeError; }
    }""")
    assert blocked  # Trusted Types refuses a plain string as HTML
    assert page.evaluate("window.__pwned") is None
    _violations.clear()  # this test causes its violations on purpose


def test_foreign_scripts_are_refused(server, page):
    page.goto(server.url)
    page.wait_for_selector("#app >> text=Tank Tactics", state="attached")
    with pytest.raises(Exception, match="Refusing to load script"):
        page.evaluate("import('/static/js/trusted.js').then((m) => m.scriptURL('https://evil.example/x.js'))")
    _violations.clear()
