"""Security headers (``api/security.py``) on every kind of response."""
from __future__ import annotations

import pytest

from tank_tactics.api.security import APP_CSP, DOCS_CSP


@pytest.mark.parametrize("path", ["/", "/static/js/main.js", "/api/defaults", "/api/nope", "/openapi.json"])
def test_app_responses_get_the_strict_policy(api, path):
    client, _, _ = api
    h = client.get(path).headers
    assert h["content-security-policy"] == APP_CSP
    script_src = next(d for d in APP_CSP.split("; ") if d.startswith("script-src"))
    assert "unsafe" not in script_src and "require-trusted-types-for 'script'" in APP_CSP
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert h["cross-origin-opener-policy"] == "same-origin-allow-popups"


def test_api_docs_get_their_own_policy(api):
    client, _, _ = api
    r = client.get("/docs")
    assert r.status_code == 200 and r.headers["content-security-policy"] == DOCS_CSP
    assert "frame-ancestors 'none'" in DOCS_CSP and "connect-src" not in DOCS_CSP
