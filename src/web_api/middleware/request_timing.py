"""Request duration response header and upload diagnostics."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.web_api.middleware.request_id import get_request_id

logger = logging.getLogger(__name__)


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Expose FastAPI processing time without including client/network time."""

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Server-Duration-Ms"] = f"{duration_ms:.2f}"
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.2f}"
        if request.method == "POST" and request.url.path.endswith("/documents"):
            logger.info(
                "upload request timing request_id=%s status=%s duration_ms=%.2f path=%s",
                get_request_id(request), response.status_code, duration_ms, request.url.path,
            )
        return response


__all__ = ["RequestTimingMiddleware"]
