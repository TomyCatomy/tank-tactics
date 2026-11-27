"""The server can speak HTTPS itself (the AWS origin does: deploy/aws/tls.sh)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tank_tactics.config import ServerConfig


def test_tls_is_off_by_default():
    cfg = ServerConfig()
    assert cfg.ssl_certfile is None and cfg.ssl_keyfile is None


def test_tls_needs_both_the_certificate_and_the_key(monkeypatch):
    monkeypatch.setenv("SSL_CERTFILE", "/tls/fullchain.pem")
    with pytest.raises(ValidationError, match="SSL_KEYFILE"):
        ServerConfig()
    monkeypatch.setenv("SSL_KEYFILE", "/tls/privkey.pem")
    cfg = ServerConfig()
    assert (cfg.ssl_certfile, cfg.ssl_keyfile) == ("/tls/fullchain.pem", "/tls/privkey.pem")
