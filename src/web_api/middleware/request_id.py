"""
``X-Request-ID`` propagation middleware.

Behaviour:

- If the client sent an ``X-Request-ID`` header, reuse it.
- Otherwise, generate a fresh ULID-ish token (timestamp + random suffix).
- Store it on ``request.state.request_id`` so handlers / exception
  handlers can read it for inclusion in error envelopes and logs.
- Echo the value back in the response header so the client can correlate.

The header name is fixed for now; should we ever need a configurable
name, lift it to a Settings field.
"""

from __future__ import annotations

import secrets
import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_ATTR = "request_id"


def _new_request_id() -> str:
    """Generate a request ID.

    Format: ``<unix_ms>-<8 hex chars>`` — short enough to be log-friendly
    while remaining unique per call. Not a real ULID, just looks similar.
    """
    return f"{int(time.time() * 1000):x}-{secrets.token_hex(4)}"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a request ID to every inbound request and response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        rid = incoming if incoming else _new_request_id()
        # Stash on request.state so downstream code (handlers, loggers,
        # exception handlers) can read it without re-parsing headers.
        setattr(request.state, _REQUEST_ID_ATTR, rid)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


def get_request_id(request: Request) -> str:
    """Read the request ID from ``request.state``; returns ``''`` if absent.

    Use this from handlers / exception handlers that need to embed the
    ID in error envelopes or log lines. The middleware always sets it,
    so the ``''`` fallback is purely defensive.
    """
    return getattr(request.state, _REQUEST_ID_ATTR, "")


__all__ = [
    "REQUEST_ID_HEADER",
    "RequestIDMiddleware",
    "get_request_id",
]
