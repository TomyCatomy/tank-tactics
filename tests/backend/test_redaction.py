"""``redaction.redact``: masks secret-looking text and leaves ordinary text alone."""
from __future__ import annotations

import pytest

from tank_tactics import redaction
from tank_tactics.redaction import MASK, redact


@pytest.mark.parametrize("text, want", [
    ("/api/discord/callback?code=abc123&state=xyz", f"/api/discord/callback?code={MASK}&state={MASK}"),
    ('{"token": "s3cr3t-tok", "name": "Ann"}', f'{{"token": "{MASK}", "name": "Ann"}}'),
    ("https://tanktactics.io/#/g/abc?t=SEATTOKEN", f"https://tanktactics.io/#/g/abc?t={MASK}"),
    ("password: hunter22", f"password: {MASK}"),
    ("client_secret='abc'", f"client_secret='{MASK}'"),
    ("postgresql://app:pw@host/db", f"postgresql://app:{MASK}@host/db"),
    ("https://discord.com/api/webhooks/42/abc-DEF_1", f"https://discord.com/api/webhooks/42/{MASK}"),
    ("https://discordapp.com/api/v10/webhooks/42/tok", f"https://discordapp.com/api/v10/webhooks/42/{MASK}"),
    ("Authorization: Bearer abc.def-ghi", f"Authorization: {MASK} {MASK}"),
    ("sent Basic dXNlcjpwYXNz", f"sent Basic {MASK}"),
    ("id eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig", f"id {MASK}"),
])
def test_masks_secrets(text, want):
    assert redact(text) == want


@pytest.mark.parametrize("text", [
    "GET /api/games/{game_id}",
    "scheduler tick failed for game abc123",
    '{"type": "state", "state": {"game": 1}}',
    "game started at 12:00, 3 players",
    "https://tanktactics.io/api/defaults",
])
def test_leaves_ordinary_text_alone(text):
    assert redact(text) == text


def test_registered_secrets_are_masked_anywhere():
    redaction.register_secret("not-a-pattern-but-secret")
    redaction.register_secret("short")  # too short to mask safely: ignored
    assert redact("x not-a-pattern-but-secret y short") == f"x {MASK} y short"


def test_env_secrets_and_otlp_header_values():
    redaction.register_env_secrets({
        "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic%20Z3JhZmFuYQ==,X-Scope=team-a-scope",
        "DISCORD_CLIENT_SECRET": "discord-client-secret-value",
        "DB_PATH": "data/tank-tactics.db",
    })
    assert redact("discord-client-secret-value") == MASK
    assert redact("Basic%20Z3JhZmFuYQ== team-a-scope") == f"{MASK} {MASK}"
    assert redact("data/tank-tactics.db") == "data/tank-tactics.db"


def test_url_attributes_lose_their_query():
    attrs = redaction.attributes({"http.url": "https://x.io/cb?code=1&other=2", "url.query": "code=1",
                                  "http.route": "/cb", "http.status_code": 303})
    assert attrs == {"http.url": "https://x.io/cb", "http.route": "/cb", "http.status_code": 303}
