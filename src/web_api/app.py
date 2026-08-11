"""
FastAPI application factory.

Single entry point used by:

- ``src/web_api/main.py`` (production uvicorn entry)
- Test suites (``from src.web_api.app import create_app``)

The factory pattern keeps state out of module-level globals, which is
essential for test isolation.

Mounted routers (15 endpoints total — 14 from spec + ``/images/{id}``):

- ``/api/v1/system``         — info + health
- ``/api/v1/collections``    — CRUD + documents
- ``/api/v1/documents``      — single-document detail + delete
- ``/api/v1/tasks``          — task status
- ``/api/v1/queries``        — query trace (GET)
- ``/api/v1/ingestions``     — ingestion trace (GET)
- ``/api/v1/images``         — controlled image access
- ``POST /api/v1/collections/{id}/queries`` — query creation
- ``GET  /docs``, ``GET /openapi.json`` — auto-generated API docs
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.application.composition import ApplicationServices
from src.web_api.exception_handlers import register_exception_handlers
from src.web_api.middleware.request_id import RequestIDMiddleware
from src.web_api.middleware.request_timing import RequestTimingMiddleware
from src.web_api.routers import (
    collections,
    documents,
    images,
    queries,
    system,
    metrics,
    tasks,
    traces,
)

API_PREFIX = "/api/v1"
"""All routes mount under this prefix — see PRODUCTION spec §5."""


def create_app(services: ApplicationServices | None = None) -> FastAPI:
    """Build a fresh FastAPI app.

    No global state; safe to call multiple times (each test gets its own).

    ``services`` — the boot application services. When ``None`` (the
    uvicorn default) they are built lazily on the first request and cached
    on ``app.state`` (see :func:`src.web_api.dependencies.get_application_services`).
    Tests pass a services object built with fakes to exercise real endpoints.
    """
    app = FastAPI(
        title="SKDY RAG Server API",
        version="0.2.0",
        description=(
            "HTTP API for the SKDY RAG Server. Mounted under `/api/v1`.\n\n"
            "**Contract**: the OpenAPI schema is the single source of truth "
            "for the Web UI; client types are generated from it.\n\n"
            "**Error envelope**: every 4xx/5xx returns "
            "`{\"error\": {\"code\": \"...\", \"message\": \"...\", "
            "\"request_id\": \"...\", \"details\": {}}}`."
        ),
        # Swagger UI lives at /docs, ReDoc at /redoc; keep both on.
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # CORS — open by default for local dev (M1). Tighten or move to a
    # Settings field once the Web UI is hosted on a different origin.
    # The CORS contract lives in PRODUCTION §13 (security).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Server-Duration-Ms", "Server-Timing"],
    )

    # Request-ID wraps timing so upload timing logs carry the same correlation id.
    app.add_middleware(RequestTimingMiddleware)
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)

    # Application services (None → lazily built on first request).
    app.state.application_services = services

    # Mount routers under the versioned prefix.
    app.include_router(system.router, prefix=API_PREFIX)
    app.include_router(collections.router, prefix=API_PREFIX)
    app.include_router(documents.router, prefix=API_PREFIX)
    app.include_router(tasks.router, prefix=API_PREFIX)
    app.include_router(metrics.router, prefix=API_PREFIX)
    app.include_router(queries.router, prefix=API_PREFIX)
    app.include_router(traces.router, prefix=API_PREFIX)
    app.include_router(images.router, prefix=API_PREFIX)

    return app


# Module-level instance for `uvicorn src.web_api.app:app` — convenience.
app = create_app()


__all__ = ["API_PREFIX", "app", "create_app"]
