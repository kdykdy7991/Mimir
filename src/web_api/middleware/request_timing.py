"""Request duration response header and upload diagnostics."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.web_api.middleware.request_id import get_request_id

logger = logging.getLogger(__name__)

# Paths whose request/response BODY must never be captured or echoed by any
# middleware logging (B4.4): the test-connection body carries a one-time MCP
# Client Key. We still add the X-Server-Duration headers, but any body-capture
# logging is disabled for these paths and no Key value is ever logged.
SENSITIVE_BODY_PATHS = frozenset({"/api/v1/mcp-server/test-connection"})


def body_capture_disabled(path: str) -> bool:
    """Return True when request/response body logging must be suppressed.

    The one-time MCP Client Key in ``test-connection`` bodies must never land
    in a log, trace or exception. Callers that perform body capture must short
    circuit on this before reading/logging``await request.body()``.
    """
    return path in SENSITIVE_BODY_PATHS


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Expose FastAPI processing time without including client/network time."""

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Server-Duration-Ms"] = f"{duration_ms:.2f}"
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.2f}"
        # Request/response bodies are never logged here; for the sensitive
        # test-connection path we additionally guard against body capture
        # (``await request.body()``) via ``body_capture_disabled`` so no
        # one-time MCP Client Key can ever be echoed (B4.4).
        if request.method == "POST" and request.url.path.endswith("/documents"):
            logger.info(
                "upload request timing request_id=%s status=%s duration_ms=%.2f path=%s",
                get_request_id(request), response.status_code, duration_ms, request.url.path,
            )
        return response


__all__ = ["RequestTimingMiddleware", "SENSITIVE_BODY_PATHS", "body_capture_disabled"]
