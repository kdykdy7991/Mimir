"""
Error DTOs shared across async business operations.

The HTTP-level ``ErrorEnvelope`` (``src.web_api.schemas.common``) is for
protocol errors — 4xx/5xx returned when the request itself fails.
``TaskError`` here is different: it's the error payload **inside a 200
response** when an async operation (e.g. an ingestion task) finished in
a failed business state. The HTTP request itself succeeded; what failed
is the work the request kicked off.

Frontend must branch on ``code`` exactly like it does for HTTP errors.
``message`` is human-only; ``details`` is opaque structured context.

The type itself now lives in the application layer
(``src.application.services.task_types``) so the app services can be
reused by CLI / MCP / Streamlit without importing the Web API. This
module re-exports it for backward compatibility.
"""

from __future__ import annotations

from src.application.services.task_types import TaskError  # noqa: F401

__all__ = ["TaskError"]
