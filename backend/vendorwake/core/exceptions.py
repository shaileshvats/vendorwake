"""Application exception hierarchy and FastAPI handlers.

Every error response has the same shape: ``{"error": {"code": str, "message": str}}`` (validation
errors add ``details`` with field locations). Messages for unexpected errors are generic so internals
never leak; the full exception goes to the logs and Sentry instead.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors that map to a deliberate HTTP response."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        self.headers = dict(headers) if headers else None
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"
    message = "Bad request"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"
    message = "Authentication required"

    def __init__(
        self, message: str | None = None, *, code: str | None = None, error: str | None = None
    ) -> None:
        # RFC 6750 section 3: no error attribute when the request carried no credentials.
        challenge = f'Bearer error="{error}"' if error else "Bearer"
        super().__init__(message, code=code, headers={"WWW-Authenticate": challenge})


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"
    message = "Permission denied"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Not found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "Conflict"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "Service unavailable"


class AuthNotConfiguredError(ServiceUnavailableError):
    code = "auth_not_configured"
    message = "Authentication is not configured"


def error_body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, **extra}}


def _status_code_name(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).name.lower()
    except ValueError:
        return "http_error"


def _status_phrase(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.warning("app error %s on %s %s", exc.code, request.method, request.url.path)
    return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status_code, headers=exc.headers)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    status_code = exc.status_code
    # 4xx details are authored by route code (or Starlette, e.g. "Not Found"); 5xx details are not exposed.
    if status_code < 500 and isinstance(exc.detail, str) and exc.detail:
        message = exc.detail
    else:
        message = _status_phrase(status_code)
    headers = getattr(exc, "headers", None)
    return JSONResponse(
        error_body(_status_code_name(status_code), message), status_code=status_code, headers=headers
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Only location, message and type: the raw "input" may contain whatever the client sent.
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return JSONResponse(
        error_body("validation_error", "Request validation failed", details=details), status_code=422
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(error_body("internal_error", "Internal server error"), status_code=500)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
