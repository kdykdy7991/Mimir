"""
FastAPI dependencies — the single way routers reach the application layer.

Routers declare ``services: ApplicationServices = Depends(get_application_services)``
and call through the four services; they never touch Python internals or
storage directly (PRODUCTION §3 dependency rules).

Tests inject services either via ``create_app(services=...)`` or by
setting ``app.state.application_services`` before making requests.
"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

from fastapi import Request

from src.web_api.settings import SETTINGS

if TYPE_CHECKING:
    from src.application.composition import ApplicationServices

DEFAULT_DATA_DIR = SETTINGS.data_dir
"""Where the app services read/write their data (``./data`` by default)."""

_services_init_lock = Lock()
"""Serialize first-use construction across FastAPI worker threads."""


def _upload_policy_from_settings():
    """Build the app-layer :class:`UploadPolicy` from the Web API settings.

    The application layer must not import ``src.web_api.settings``; the
    boot layer translates the env-driven Web API settings into the
    application's upload policy so batch per-file validation (in
    ``IngestionService``) and the router's HTTP-level checks stay
    consistent.
    """
    from src.application.services import UploadPolicy

    return UploadPolicy(
        max_file_bytes=SETTINGS.upload_max_bytes,
        allowed_extensions=frozenset(SETTINGS.upload_allowed_extensions),
        allowed_mime=frozenset(SETTINGS.upload_allowed_mime),
        max_batch_files=SETTINGS.upload_max_batch_files,
        max_batch_bytes=SETTINGS.upload_max_batch_bytes,
    )


def get_application_services(request: Request) -> "ApplicationServices":
    """Return the boot services, building them lazily on first use.

    Building the full RAG stack (vector store + SQLite + BM25) is
    expensive, so it happens once per process and is cached on
    ``app.state`` — never on every request.
    """
    services = getattr(request.app.state, "application_services", None)
    if services is None:
        # FastAPI executes this synchronous dependency in a thread pool. The
        # overview page issues several requests in parallel, so first use can
        # enter here concurrently. Chroma's SharedSystemClient initialization
        # is not safe under that race; use double-checked locking so exactly
        # one application service graph is built per app.
        with _services_init_lock:
            services = getattr(request.app.state, "application_services", None)
            if services is None:
                from src.application.composition import build_application_services

                services = build_application_services(
                    data_dir=DEFAULT_DATA_DIR,
                    upload_policy=_upload_policy_from_settings(),
                )
                request.app.state.application_services = services
    return services


__all__ = ["DEFAULT_DATA_DIR", "get_application_services"]
