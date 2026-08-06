"""
Stable id derivation for the application layer.

The Web API exposes collections / documents as UUIDs while storage
identifies them by *name* (collection) and *source_path*. These two
pure functions map back and forth — they live here (application
layer) so ``QueryService`` can derive document ids for query-result
recording (``last_query_id``) without importing the Web API layer.
``src.web_api.mappers`` re-exports them so routers keep their existing
imports.
"""

from __future__ import annotations

from uuid import UUID, uuid5

# Same value as uuid.NAMESPACE_DNS — a fixed, stable namespace.
_NAMESPACE = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def collection_uuid(name: str) -> UUID:
    """Stable collection id derived from the collection name."""
    return uuid5(_NAMESPACE, f"collection:{name}")


def document_uuid(collection: str, source_path: str) -> UUID:
    """Stable document id derived from (collection, source_path)."""
    return uuid5(_NAMESPACE, f"document:{collection}:{source_path}")


__all__ = ["collection_uuid", "document_uuid"]
