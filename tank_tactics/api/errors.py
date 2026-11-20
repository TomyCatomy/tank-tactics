"""Every error leaves the API as ``{"error": {"code": ..., "message": ...}}``."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..game import ActionType
from ..service import ServiceError

log = logging.getLogger("tank_tactics")


def error_response(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _describe_validation(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        if err.get("type") == "json_invalid":
            return "Body must be valid JSON."
        if err.get("type") in ("union_tag_invalid", "union_tag_not_found"):
            # The only tagged union in the API is the player Action.
            parts.append(f"'type' must be one of: {', '.join(t.value for t in ActionType)}")
            continue
        # Drop the leading "body" and union-variant names from the location.
        loc = [str(x) for x in err.get("loc", ()) if x != "body"]
        parts.append(f"{'.'.join(loc)}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts) or "Invalid request."


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return error_response(exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response("bad_request", _describe_validation(exc), 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 413: "too_large"}.get(exc.status_code, "http_error")
        return error_response(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error", exc_info=exc)
        return error_response("internal", "Internal server error.", 500)
