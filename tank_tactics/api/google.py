"""Verifying "Sign in with Google" ID tokens.

The browser gets an ID token (a JWT signed by Google) from Google Identity Services
and posts it to ``/api/auth/google``. We check the signature against Google's
published keys (fetched and cached by PyJWT), the audience (our client ID), the
issuer and the expiry, then use the stable ``sub`` claim as the account key.

The verifier is a plain callable so tests can swap in a fake one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import jwt

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class InvalidGoogleToken(Exception):
    """The ID token is malformed, forged, expired or meant for another app."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str  # Google's stable account id
    name: str


GoogleVerifier = Callable[[str], GoogleIdentity]


class GoogleTokenVerifier:
    def __init__(self, client_id: str, jwks_client: Any = None, leeway: float = 60):
        self.client_id = client_id
        self.leeway = leeway
        # PyJWKClient caches the key set and refetches when it sees an unknown key id.
        self._jwks = jwks_client or jwt.PyJWKClient(GOOGLE_CERTS_URL, cache_keys=True, lifespan=3600, timeout=10)

    def __call__(self, credential: str) -> GoogleIdentity:
        try:
            key = self._jwks.get_signing_key_from_jwt(credential).key
            claims = jwt.decode(
                credential, key, algorithms=["RS256"], audience=self.client_id, leeway=self.leeway,
                options={"require": ["iss", "sub", "aud", "exp", "iat"]},
            )
        except jwt.PyJWTError as e:  # includes key fetch failures (PyJWKClientError)
            raise InvalidGoogleToken(str(e)) from None
        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise InvalidGoogleToken("wrong issuer")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise InvalidGoogleToken("missing subject")
        name = claims.get("given_name") or claims.get("name") or "Player"
        return GoogleIdentity(sub=sub, name=str(name))
