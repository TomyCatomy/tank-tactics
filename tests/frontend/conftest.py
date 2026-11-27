"""Fixtures for the frontend (browser) tests.

The real page runs in headless Edge (or Playwright's bundled Chromium) against the
real app served by uvicorn in a background thread. Every test in this directory is
marked ``ui``, so ``pytest -m "not ui"`` skips them; they are also skipped when
Playwright or a browser isn't available.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from websockets.exceptions import InvalidState

from tank_tactics.api import create_app

from ..support import FAKE_GOOGLE_CLIENT_ID, fake_google_verifier, make_service

_HERE = Path(__file__).parent


def pytest_collection_modifyitems(items):
    for item in items:
        if _HERE in Path(item.path).parents:
            item.add_marker(pytest.mark.ui)


class LiveServer:
    """Runs the app on a fixed port in a background thread; can be stopped and restarted."""

    def __init__(self, service, google_verifier=None, discord_connect=None):
        self.service = service
        self.google_verifier = google_verifier
        self.discord_connect = discord_connect
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        # A fresh app each time, like a real restart (new event loop, new Hub).
        config = uvicorn.Config(create_app(self.service, self.google_verifier, self.discord_connect), host="127.0.0.1", port=self.port,
                                log_level="warning", timeout_graceful_shutdown=1)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, args=(self._server,), daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.02)

    @staticmethod
    def _run(server: uvicorn.Server) -> None:
        try:
            server.run()
        except InvalidState:
            # Upstream race: on shutdown uvicorn sends a close frame to every open
            # WebSocket, which raises if that socket is already closing. Harmless once
            # we've asked the server to exit; anything else still fails loudly.
            if not server.should_exit:
                raise

    def stop(self) -> None:
        assert self._server and self._thread
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def server():
    """A running LiveServer over an in-memory service; ``server.stop()/start()`` simulates an outage."""
    svc, _ = make_service(action_rate_per_second=100)
    srv = LiveServer(svc)
    srv.start()
    yield srv
    if srv.running:
        srv.stop()


@pytest.fixture
def auth_server():
    """A LiveServer with Google sign-in on, using the fake verifier from ``tests/support.py``
    (pair it with ``fake_google(page)`` so the browser gets a fake Google button)."""
    svc, _ = make_service(action_rate_per_second=100, google_client_id=FAKE_GOOGLE_CLIENT_ID)
    srv = LiveServer(svc, fake_google_verifier)
    srv.start()
    yield srv
    if srv.running:
        srv.stop()


@pytest.fixture
def server_without_debug():
    """Like ``server``, but configured as production: ``allow_debug`` is off."""
    svc, _ = make_service(action_rate_per_second=100, allow_debug=False)
    srv = LiveServer(svc)
    srv.start()
    yield srv
    if srv.running:
        srv.stop()


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        launched = None
        for kwargs in ({"channel": "msedge"}, {}):
            try:
                launched = pw.chromium.launch(headless=True, **kwargs)
                break
            except Exception:
                continue
        if launched is None:
            message = "no browser available: install Edge or run `python -m playwright install chromium`"
            if os.environ.get("CI"):  # in CI a missing browser must not turn into a green run
                pytest.fail(message)
            pytest.skip(message)
        yield launched
        launched.close()


@pytest.fixture
def page(browser):
    """A fresh page in its own context (separate localStorage per test)."""
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()
