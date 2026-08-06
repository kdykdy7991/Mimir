"""
Dashboard service layer — thin facades over ingestion /
storage / observability code so page modules don't have to
know about the underlying plumbing.

Each service is defined in its own module and imported
directly by the page that needs it — this ``__init__`` only
re-exports the cross-cutting services that multiple pages
share. ``DataService`` (G3) and ``TraceService`` (G5) will
be added as those pages come online; keeping them out of the
top-level import avoids G1 breaking on G3/G5 imports.
"""

from src.observability.dashboard.services.config_service import (
    ComponentCard,
    ConfigService,
)

__all__ = [
    "ComponentCard",
    "ConfigService",
]
