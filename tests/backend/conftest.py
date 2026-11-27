"""Fixtures for the backend tests (rules engine, service, persistence, HTTP/WebSocket API)."""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tank_tactics import database

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


@pytest.fixture(params=["sqlite", "postgres"])
def db(request, tmp_path):
    """Where a persistence test keeps its database: a SQLite file, then (when
    ``TEST_DATABASE_URL`` names a Postgres database, such as a Neon direct URL) a
    throwaway schema there that is dropped afterwards."""
    if request.param == "sqlite":
        yield str(tmp_path / "t.db")
        return
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    schema = f"tt_test_{uuid.uuid4().hex[:12]}"
    admin = database.make_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield url + ("&" if "?" in url else "?") + f"options=-csearch_path%3D{schema}"
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
