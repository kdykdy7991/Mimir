"""
Unified exception handlers.

All errors leave the API in the same envelope:

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": {}}}

Three conversion paths are covered:

- :class:`APIError` (our domain errors) → matching HTTP status.
- :class:`fastapi.HTTPException` (FastAPI's own 4xx, e.g. 422 from
  Pydantic) → re-mapped onto the same envelope.
- Any other ``Exception`` → 500 with code ``INTERNAL_ERROR`` and a
  generic message. The full traceback is logged for diagnosis but never
  returned to the client (avoid leaking internals).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.web_api.errors import APIError
from src.web_api.middleware.request_id import get_request_id
from src.web_api.schemas.common import ErrorDetail, ErrorEnvelope

logger = logging.getLogger(__name__)


def _envelope(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: dict | None = None,
) -> JSONResponse:
    """Build a JSON response with the standard error envelope."""
    payload = ErrorEnvelope(
        error=ErrorDetail(
            code=code,
            message=message,
            request_id=request_id or None,
            details=details or {},
        ),
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(
            payload.model_dump(exclude_none=True),
            custom_encoder={Exception: str},
        ),
    )


def _http_exception_code(status_code: int) -> str:
    """Map a generic HTTP status to a stable error code.

    Used for ``HTTPException`` raised by FastAPI/Starlette internals so
    the frontend always has *some* code to branch on.
    """
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
    }.get(status_code, "HTTP_ERROR")


async def _api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return _envelope(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request_id=get_request_id(request),
        details=exc.details,
    )


async def _validation_error_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """Pydantic body/query validation errors → 422 with field details."""
    # Starlette renamed 422 → 422 Unprocessable Content in 0.40. Fall
    # back to the numeric value without touching the deprecated alias.
    status_422 = getattr(
        status, "HTTP_422_UNPROCESSABLE_CONTENT",
        422,
    )
    return _envelope(
        status_code=status_422,
        code="VALIDATION_ERROR",
        message="Request validation failed.",
        request_id=get_request_id(request),
        details={"errors": _redact_validation_errors(exc)},
    )


# Field names that, if they appear in a validation ``loc``, must never have
# their raw ``input`` value echoed (B4.4 — the MCP Client Key is a one-time
# secret; the Authorization header never leaves the request layer).
_SENSITIVE_FIELD_TOKENS = ("api_key", "secret", "token", "password", "authorization")


def _redact_validation_errors(exc: RequestValidationError) -> list[dict]:
    """Return ``exc.errors()`` with sensitive field inputs redacted.

    Pydantic's default error detail carries the offending ``input`` value;
    for a ``SecretStr``/token/key field that value is the secret itself and
    must not reach the response, log or frontend.
    """
    out: list[dict] = []
    for err in exc.errors():
        err = dict(err)
        loc = err.get("loc") or ()
        parts = [str(part).lower() for part in loc]
        if any(any(tok in part for tok in _SENSITIVE_FIELD_TOKENS) for part in parts):
            err["input"] = "<redacted>"
        out.append(err)
    return out


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException | HTTPException,
) -> JSONResponse:
    """FastAPI / Starlette HTTPException → unified envelope."""
    code = _http_exception_code(exc.status_code)
    # 404 for unknown paths is normal during dev — keep message terse.
    message = exc.detail if isinstance(exc.detail, str) else code
    return _envelope(
        status_code=exc.status_code,
        code=code,
        message=str(message),
        request_id=get_request_id(request),
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception,
) -> JSONResponse:
    """Catch-all: log full traceback, return generic 500.

    Avoid leaking internals (file paths, library names, SQL) to clients.
    """
    logger.exception(
        "unhandled exception on %s %s (request_id=%s)",
        request.method, request.url.path, get_request_id(request),
    )
    return _envelope(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="An unexpected error occurred. Please try again later.",
        request_id=get_request_id(request),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the handlers above onto a FastAPI app.

    Order matters: register the most specific first; ``Exception`` is
    the catch-all and must be last.
    """
    app.add_exception_handler(APIError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


__all__ = ["register_exception_handlers"]
