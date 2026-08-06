"""
Chroma VectorStore implementation.

Uses ChromaDB for local vector storage.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    QueryResult,
    VectorRecord,
    VectorStoreError,
)

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
except ImportError:
    chromadb = None  # type: ignore
    ChromaSettings = None  # type: ignore


# ChromaDB metadata values must be scalar (str/int/float/bool) or uniform
# lists of those scalars. Nested dicts/lists are rejected.
_CHROMA_ALLOWED_SCALAR = (str, int, float, bool)

# ChromaDB collection names are restricted to ``[a-zA-Z0-9._-]``, 3-512
# chars, starting and ending with an alphanumeric (see the "Expected a
# name containing 3-512 characters..." InvalidArgumentError). Mirror that
# exactly so the pass-through check below can never surprise us.
_CHROMA_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,510}[a-zA-Z0-9]$")


def chroma_collection_name(display_name: str) -> str:
    """Map a knowledge-base display name to a Chroma-safe collection name.

    ChromaDB rejects non-ASCII names (Chinese knowledge bases included), so
    the vector store needs an internal name that satisfies Chroma's charset
    while the rest of the system keeps the human-readable display name
    (BM25 index files, integrity records, document ids, the API).

    Rule: a display name that is already Chroma-valid is returned verbatim
    (existing collections like ``default`` / ``test`` keep their literal
    Chroma name — no migration, still greppable). Anything else — Chinese,
    too short, edge punctuation — maps deterministically to
    ``coll_<uuid5hex>`` so the same display name always resolves to the
    same Chroma collection. The mapping is a pure function: no lookup table,
    stable across restarts and processes.
    """
    if _CHROMA_NAME_RE.match(display_name):
        return display_name
    return "coll_" + uuid.uuid5(uuid.NAMESPACE_URL, display_name).hex


def _sanitize_metadata_value(value: Any) -> Any | None:
    """Return a ChromaDB-compatible value, or None to drop the key."""
    if isinstance(value, _CHROMA_ALLOWED_SCALAR):
        return value
    if isinstance(value, list):
        if not value:
            return value
        if not all(isinstance(v, _CHROMA_ALLOWED_SCALAR) for v in value):
            return None
        first_type = type(value[0])
        if not all(type(v) is first_type for v in value):
            return None
        return value
    return None


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Drop metadata fields that ChromaDB cannot store."""
    clean: dict[str, Any] = {}
    for key, value in metadata.items():
        sanitized = _sanitize_metadata_value(value)
        if sanitized is not None:
            clean[key] = sanitized
    return clean


class ChromaStore(BaseVectorStore):
    """
    ChromaDB VectorStore provider.

    Uses ChromaDB for local vector storage with persistent directory.
    """

    def __init__(self, settings: Any):
        if chromadb is None:
            raise VectorStoreError(
                "chromadb package is not installed. "
                "Install it with: pip install chromadb"
            )
        self.settings = settings
        self._persist_directory = getattr(settings, "persist_path", "./data/db/chroma")
        self._collection_name = getattr(settings, "collection_name", "default")

        # Create client with persistence
        self._client = chromadb.PersistentClient(
            path=self._persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        # Get or create collection. ``_collection_name`` keeps the display
        # name (stats / callers see it); the Chroma handle binds the
        # Chroma-safe mapped name so non-ASCII collections (e.g. Chinese
        # knowledge bases) work too.
        self._collection = self._client.get_or_create_collection(
            name=chroma_collection_name(self._collection_name),
        )

    def upsert(self, records: list[VectorRecord], **kwargs: Any) -> int:
        """
        Insert or update records in ChromaDB.

        Args:
            records: List of VectorRecord objects.
            **kwargs: Additional parameters.

        Returns:
            int: Number of records upserted.
        """
        if not records:
            return 0

        try:
            ids = [r.id for r in records]
            embeddings = [r.vector for r in records]
            documents = [r.text for r in records]
            # ChromaDB metadata only accepts scalar values or uniform lists
            # of str/int/float/bool. Drop nested dicts/lists (e.g.
            # ``images``, ``image_captions``) automatically.
            metadatas = [_sanitize_metadata(r.metadata) for r in records]

            self._collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
            return len(records)
        except Exception as e:
            raise VectorStoreError(f"ChromaDB upsert failed: {e}") from e

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[QueryResult]:
        """
        Query ChromaDB for similar vectors.

        Args:
            vector: Query vector.
            top_k: Number of results.
            filters: Optional metadata filters.

        Returns:
            list[QueryResult]: Matching results.
        """
        try:
            # Convert filters to ChromaDB format
            where = None
            if filters:
                where = filters

            results = self._collection.query(
                query_embeddings=[vector],
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )

            # Parse results
            query_results = []
            if results and results["ids"] and results["ids"][0]:
                ids = results["ids"][0]
                documents = results["documents"][0] if results["documents"] else [""] * len(ids)
                metadatas = results["metadatas"][0] if results["metadatas"] else [{}] * len(ids)
                distances = results["distances"][0] if results["distances"] else [0.0] * len(ids)

                for i, id in enumerate(ids):
                    # ChromaDB returns distances, convert to similarity score
                    score = 1.0 - distances[i] if distances[i] <= 1.0 else 0.0
                    query_results.append(QueryResult(
                        id=id,
                        score=score,
                        text=documents[i],
                        metadata=metadatas[i],
                    ))

            return query_results
        except Exception as e:
            raise VectorStoreError(f"ChromaDB query failed: {e}") from e

    def delete(self, ids: list[str], **kwargs: Any) -> int:
        """
        Delete records from ChromaDB.

        Args:
            ids: List of record IDs to delete.
            **kwargs: Additional parameters.

        Returns:
            int: Number of records deleted.
        """
        if not ids:
            return 0

        try:
            self._collection.delete(ids=ids)
            return len(ids)
        except Exception as e:
            raise VectorStoreError(f"ChromaDB delete failed: {e}") from e

    def get_by_ids(
        self,
        ids: list[str],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Fetch stored records by ID. See ``BaseVectorStore``."""
        if not ids:
            return []
        try:
            # ChromaDB's ``get`` always returns a dict whose lists
            # are aligned with the input ``ids`` order. Unknown
            # IDs are simply absent from the response.
            result = self._collection.get(
                ids=ids,
                include=["documents", "metadatas"],
            )
            out: list[dict[str, Any]] = []
            docs = result.get("documents") or [""] * len(result["ids"])
            metas = result.get("metadatas") or [{}] * len(result["ids"])
            for id_, doc, meta in zip(result["ids"], docs, metas):
                out.append({
                    "id": id_,
                    "text": doc or "",
                    "metadata": dict(meta or {}),
                })
            return out
        except Exception as e:
            raise VectorStoreError(f"ChromaDB get_by_ids failed: {e}") from e

    def count(self, **kwargs: Any) -> int:
        """Get the number of records in the collection."""
        return self._collection.count()

    def get_by_metadata(
        self,
        filters: dict[str, Any],
        *,
        limit: int | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Fetch all records whose metadata matches ``filters``.

        Used by :class:`DocumentManager` to list / inspect the
        chunks of a given document. ``filters`` is a flat
        ``{key: value}`` dict (Chroma's ``where`` clause format).

        Order is whatever Chroma returns — not stable across
        calls. Callers that need a stable order should sort the
        result themselves.
        """
        if not filters:
            return []
        try:
            result = self._collection.get(
                where=filters,
                include=["documents", "metadatas"],
                limit=limit,
            )
            ids = result.get("ids") or []
            docs = result.get("documents") or [""] * len(ids)
            metas = result.get("metadatas") or [{}] * len(ids)
            return [
                {
                    "id": id_,
                    "text": doc or "",
                    "metadata": dict(meta or {}),
                }
                for id_, doc, meta in zip(ids, docs, metas)
            ]
        except Exception as e:
            raise VectorStoreError(
                f"ChromaDB get_by_metadata failed: {e}",
            ) from e

    def delete_by_metadata(
        self,
        filters: dict[str, Any],
        **kwargs: Any,
    ) -> int:
        """
        Delete every record whose metadata matches ``filters``.

        Returns the number of records deleted (0 if none matched).
        Unlike :meth:`delete`, the caller does not need to know
        the IDs in advance — useful for
        :class:`DocumentManager.delete_document` which keys on
        ``source_path`` rather than chunk IDs.
        """
        if not filters:
            return 0
        try:
            result = self._collection.get(where=filters, include=[])
            ids = result.get("ids") or []
            if not ids:
                return 0
            self._collection.delete(ids=ids)
            return len(ids)
        except Exception as e:
            raise VectorStoreError(
                f"ChromaDB delete_by_metadata failed: {e}",
            ) from e

    def get_collection_stats(
        self, **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Lightweight stats summary for the Overview page.

        Returns a dict with ``count`` (total chunks) and
        ``collection_name`` — enough to render a one-liner
        without materialising every record.
        """
        return {
            "collection_name": self._collection_name,
            "count": self._collection.count(),
        }
