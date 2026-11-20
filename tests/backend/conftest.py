"""Fixtures for the backend tests (rules engine, service, persistence, HTTP/WebSocket API)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tank_tactics.api import create_app

from ..support import make_service


@pytest.fixture
def api():
    """(client, service, clock): a FastAPI TestClient over an in-memory service.

    Entering the client runs the app lifespan, so the Hub and scheduler are live.
    """
    svc, clock = make_service(action_rate_per_second=100)
    with TestClient(create_app(svc)) as client:
        yield client, svc, clock
