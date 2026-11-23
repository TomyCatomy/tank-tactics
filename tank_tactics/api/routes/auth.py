"""Optional "Sign in with Google" accounts.

Accounts are an extra layer over the per-game seat tokens: playing never requires
one. A signed-in player's seats are linked to their account, so they can list them
and open them on any device (``/api/me/games``, ``/claim``).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ...service import ServiceError
from ..deps import ServiceDep, SessionTokenDep, UserDep
from ..google import InvalidGoogleToken
from ..schemas import AuthConfig, Credentials, GoogleSignInRequest, Ok, SignedIn, UserGame, UserOut

router = APIRouter(prefix="/api", tags=["accounts"])


@router.get("/auth/config")
def auth_config(service: ServiceDep) -> AuthConfig:
    """Whether Google sign-in is available, and the client ID the browser needs for it."""
    return AuthConfig(google_client_id=service.cfg.google_client_id)


@router.post("/auth/google")
def sign_in_with_google(body: GoogleSignInRequest, request: Request, service: ServiceDep) -> SignedIn:
    """Exchange a Google ID token (the ``credential`` from Google Identity Services)
    for a session token. Send that as ``Authorization: Bearer`` to the account endpoints."""
    verify = request.app.state.google_verifier
    if verify is None:
        raise ServiceError("not_found", "Google sign-in is not enabled on this server.", 404)
    client = request.client.host if request.client else "unknown"
    if not service.login_limiter.allow(client):
        raise ServiceError("rate_limited", "Too many sign-in attempts. Try again in a minute.", 429)
    try:
        identity = verify(body.credential)
    except InvalidGoogleToken:
        raise ServiceError("unauthorized", "Google sign-in failed. Try again.", 401) from None
    return SignedIn(**service.sign_in(identity.sub, identity.name))


@router.post("/auth/logout")
def sign_out(token: SessionTokenDep, service: ServiceDep) -> Ok:
    """End this session. Seat tokens already saved on the device keep working."""
    service.sign_out(token)
    return Ok()


@router.get("/me")
def me(user: UserDep) -> UserOut:
    return UserOut(id=user.id, name=user.name)


@router.get("/me/games")
def my_games(user: UserDep, service: ServiceDep) -> list[UserGame]:
    """Every seat linked to your account, newest game first."""
    return [UserGame(**g) for g in service.user_games(user.id)]


@router.post("/me/games/{game_id}/claim")
def claim_seat(game_id: str, user: UserDep, service: ServiceDep) -> Credentials:
    """A fresh seat token for your seat in this game, to play it on this device."""
    return Credentials(**service.claim_seat(game_id, user.id))
