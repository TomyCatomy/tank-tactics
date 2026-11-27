"""Security headers on every response.

The client keeps seat and session tokens in localStorage, so script running in the page
could steal every seat on the device. The Content-Security-Policy is the backstop if a
bug ever lets attacker-controlled text become markup or script: only our own scripts and
Google's sign-in library may run (inline styles are allowed, inline scripts are not), and
with Trusted Types the browser refuses to turn strings into HTML or script URLs except
through the client's one vetted policy (``public/js/trusted.js``).

The API docs (Swagger UI from jsdelivr, with an inline bootstrap script) get a looser
policy of their own. They hold no tokens and render no user data.
"""
from __future__ import annotations

GOOGLE_GSI = "https://accounts.google.com/gsi/"

APP_CSP = "; ".join([
    "default-src 'self'",
    f"script-src 'self' {GOOGLE_GSI}client",
    # Inline styles are allowed: Google's sign-in button sets some, and CSS can't run code.
    f"style-src 'self' 'unsafe-inline' {GOOGLE_GSI}style",
    f"frame-src {GOOGLE_GSI}",
    f"connect-src 'self' {GOOGLE_GSI}",
    "img-src 'self' data:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "require-trusted-types-for 'script'",
    # Our policy, plus the ones Google's sign-in library creates for itself.
    "trusted-types tank-tactics goog#html",
])

DOCS_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'",
    "style-src 'self' https://cdn.jsdelivr.net",
    "img-src 'self' data: https://fastapi.tiangolo.com",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
])

DOCS_PATHS = ("/docs", "/redoc")

COMMON = {
    "X-Content-Type-Options": "nosniff",
    # Google's sign-in button checks the page origin via the referrer, so send the origin
    # (never the path) cross-origin. Rejoin tokens live in the URL fragment, which browsers
    # never put in a referrer.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    # Google's sign-in popup needs to talk back to this window.
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    # Ignored over plain http (localhost); on the deployed https site, browsers stop
    # trying http for a year, so a token is never sent where it could be read.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


def headers_for(path: str) -> dict[str, str]:
    csp = DOCS_CSP if path.startswith(DOCS_PATHS) else APP_CSP
    return {**COMMON, "Content-Security-Policy": csp}
