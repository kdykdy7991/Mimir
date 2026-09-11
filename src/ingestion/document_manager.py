"""
Document Manager (G2) — cross-store document lifecycle.

A document is identified by its ``source_path`` (the original
file path the user uploaded). One document can fan out into four
storage backends:

* :class:`ChromaStore`         — dense vectors, keyed on chunk_id
* :class:`BM25Indexer`         — sparse inverted index
* :class:`ImageStorage`        — extracted images (PDF pages)
* :class:`FileIntegrityChecker`— skip / dedup history

This module is the single place that knows how to keep those
four in sync. The dashboard's data-browser and ingestion
manager pages call into here; nothing else should touch all
four backends directly.

Design choices
--------------
* **``source_path`` is the document key.** It's what the user
  sees in the UI and what they pass to ``delete_document``.
* **No global transaction.** Each store is a separate system
  with its own persistence. If Chroma succeeds but BM25 fails,
  we log a warning and continue — better to have an orphan
  somewhere than to deadlock the dashboard. The next
  ``list_documents`` call will reveal the inconsistency.
* **Read-only by default.** The only mutating method is
  ``delete_document``; the rest just summarise.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.ingestion.storage.bm25_locks import bm25_write_lock
from src.libs.loader.pdf_loader import DOC_HASH_LEN

logger = logging.getLogger(__name__)


def _image_doc_hash(file_hash: str) -> str:
    """Return the short doc_hash used by ImageStorage.

    PdfLoader computes ``doc_hash`` as the first ``DOC_HASH_LEN``
    characters of the file's SHA256, while the integrity checker
    stores the full 64-character digest. ImageStorage indexes by
    the short form, so we must truncate when looking images up.
    """
    return file_hash[:DOC_HASH_LEN]


# ---------------------------------------------------------------------------
# Protocols — DocumentManager only depends on the *shape* it needs,
# not on concrete classes. This keeps tests easy and lets us swap
# ChromaStore for a fake.
# ---------------------------------------------------------------------------

class _VectorStoreLike(Protocol):
    """Subset of BaseVectorStore that DocumentManager uses."""
    def get_by_metadata(
        self, filters: dict[str, Any], *, limit: int | None = None, **kw: Any,
    ) -> list[dict[str, Any]]: ...
    def delete(self, ids: list[str], **kw: Any) -> int: ...
    def get_collection_stats(
        self, **kw: Any,
    ) -> dict[str, Any]: ...


class _BM25Like(Protocol):
    """Subset of BM25Indexer that DocumentManager uses."""
    def remove_documents(
        self,
        index: Any,
        chunk_ids: list[str],
        trace: Any = None,
    ) -> Any: ...


class _ImageStorageLike(Protocol):
    """Subset of ImageStorage that DocumentManager uses."""
    def find_by_doc_hash(
        self, doc_hash: str, limit: int | None = None,
    ) -> list[Any]: ...
    def image_counts(
        self, *, collections: list[str] | None = None,
    ) -> dict[str, int]: ...
    def count_by_doc_hashes(
        self,
        doc_hashes: set[str],
        *,
        collection: str | None = None,
    ) -> dict[tuple[str | None, str], int]: ...


class _FileIntegrityLike(Protocol):
    """Subset of FileIntegrityChecker that DocumentManager uses."""
    def get_record_by_path(
        self, file_path: str,
    ) -> Any | None: ...
    def forget(self, file_hash: str) -> bool: ...
    def list_processed(
        self, *, status: str | None = None, collection: str | None = None,
        limit: int | None = None, offset: int | None = None,
        q: str | None = None, file_type: str | None = None,
        updated_after: float | None = None, updated_before: float | None = None,
        sort: str | None = None,
        source_paths_include: list[str] | None = None,
    ) -> list[Any]: ...
    def count(
        self, *, status: str | None = None, collection: str | None = None,
        q: str | None = None, file_type: str | None = None,
        updated_after: float | None = None, updated_before: float | None = None,
        source_paths_include: list[str] | None = None,
    ) -> int: ...
    def count_by_collection(
        self, *, collections: list[str] | None = None,
    ) -> dict[str, int]: ...


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DocumentInfo:
    """One row in the data-browser / ingestion-manager table."""
    source_path: str
    collection: str
    n_chunks: int
    n_images: int
    file_size: int | None = None
    last_modified: float | None = None
    status: str = "success"  # "success" | "failed"
    file_hash: str | None = None
    created_at: float | None = None
    updated_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "collection": self.collection,
            "n_chunks": self.n_chunks,
            "n_images": self.n_images,
            "file_size": self.file_size,
            "last_modified": self.last_modified,
            "status": self.status,
            "file_hash": self.file_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class DocumentDetail:
    """Full detail of one document (chunks + metadata + images)."""
    info: DocumentInfo
    chunks: list[dict[str, Any]] = field(default_factory=list)
    images: list[Any] = field(default_factory=list)


@dataclass
class DeleteResult:
    """Outcome of ``delete_document`` — per-store counts."""
    source_path: str
    collection: str
    chunks_deleted: int = 0
    bm25_removed: int = 0
    images_deleted: int = 0
    integrity_forgotten: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.chunks_deleted + self.bm25_removed
            + self.images_deleted
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "collection": self.collection,
            "chunks_deleted": self.chunks_deleted,
            "bm25_removed": self.bm25_removed,
            "images_deleted": self.images_deleted,
            "integrity_forgotten": self.integrity_forgotten,
            "total": self.total,
            "errors": list(self.errors),
        }


@dataclass
class CollectionStats:
    """Summary card data for the overview / data-browser header."""
    collection: str
    n_documents: int
    n_chunks: int
    n_images: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "n_documents": self.n_documents,
            "n_chunks": self.n_chunks,
            "n_images": self.n_images,
        }


@dataclass
class CorpusOverviewStats:
    """Cheap corpus totals for operational overview pages.

    Unlike :meth:`list_documents`, this aggregate never loads per-document
    chunks or images.  It reads ingestion history once and asks the vector
    store once per collection for its total chunk count.
    """

    n_documents: int
    n_chunks: int
    statuses: tuple[str, ...] = ()


@dataclass
class CollectionRef:
    """One known collection (name + index-file timestamps).

    ``created_at`` / ``updated_at`` are the BM25 index file's ctime /
    mtime — the only durable "collection" marker in the current storage.
    """
    name: str
    created_at: float | None = None
    updated_at: float | None = None


# ---------------------------------------------------------------------------
# DocumentManager
# ---------------------------------------------------------------------------

class DocumentManager:
    """
    Cross-store document lifecycle manager.

    Usage::

        mgr = DocumentManager(
            chroma_store=chroma,
            bm25_indexer=bm25,
            bm25_index=bm25_index,  # in-memory BM25Index
            image_storage=images,
            file_integrity=checker,
        )
        docs = mgr.list_documents()
        mgr.delete_document("/path/to/x.pdf", "default")

    Parameters are duck-typed via Protocols — concrete classes
    are :class:`ChromaStore`, :class:`BM25Indexer`,
    :class:`ImageStorage`, and
    :class:`SQLiteIntegrityChecker`.
    """

    def __init__(
        self,
        *,
        chroma_store: _VectorStoreLike,
        bm25_indexer: _BM25Like,
        bm25_index: Any,
        image_storage: _ImageStorageLike,
        file_integrity: _FileIntegrityLike,
    ) -> None:
        self._chroma = chroma_store
        self._bm25_indexer = bm25_indexer
        self._bm25_index = bm25_index
        self._images = image_storage
        self._integrity = file_integrity

    # ------------------------------------------------------------------
    # Collaborator access (read-only, so higher layers never swap
    # storage mid-lifecycle)
    # ------------------------------------------------------------------
    @property
    def image_storage(self):
        """The :class:`ImageStorage` used to index extracted images.

        Exposed for the Web API's ``GET /images/{id}`` endpoint (M2
        batch 3) — serving bytes is an application-layer concern, but
        the storage instance itself is owned by the manager.
        """
        return self._images

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------
    def list_documents(
        self, *, collection: str | None = None,
    ) -> list[DocumentInfo]:
        """
        Enumerate known documents.

        Reads from :meth:`FileIntegrityChecker.list_processed`
        (which is the system-of-record for "files we've seen")
        and joins with chunk + image counts from the other
        stores.

        When ``collection`` is set the listing is filtered to
        rows whose ``(collection, file_hash)`` lives in that
        collection — fixing the M3 pre-fix bug where every
        non-default collection page returned the full corpus
        under the wrong label.

        Documents with no chunks in the vector store (e.g.
        after a partial delete) are still listed, with
        ``n_chunks=0`` — that visibility is the whole point of
        this method.
        """
        records = self._integrity.list_processed(collection=collection)
        out: list[DocumentInfo] = []
        for rec in records:
            rec_collection = rec.collection or (collection or "default")
            try:
                n_chunks = len(
                    self._chroma.get_by_metadata(
                        {"source_path": rec.file_path},
                        collection=rec_collection,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "chroma lookup failed for %s: %s", rec.file_path, exc,
                )
                n_chunks = 0
            n_images = len(
                self._images.find_by_doc_hash(
                    _image_doc_hash(rec.file_hash),
                    collection=rec_collection,
                )
            )
            out.append(DocumentInfo(
                source_path=rec.file_path,
                collection=rec_collection,
                n_chunks=n_chunks,
                n_images=n_images,
                file_size=rec.file_size,
                last_modified=rec.last_modified,
                status=rec.status,
                file_hash=rec.file_hash,
                created_at=rec.created_at,
                updated_at=rec.updated_at,
            ))
        return out

    # ------------------------------------------------------------------
    # Paged list + aggregate stats (cheaper paths for the Web API)
    # ------------------------------------------------------------------
    def list_document_keys(
        self, *, collection: str | None = None,
    ) -> list[tuple[str, str]]:
        """Return ``(collection, source_path)`` pairs for every known doc.

        Unlike :meth:`list_documents`, this never touches Chroma or the
        image store — one integrity listing is enough. Used by the
        document-id index so a UUID can be resolved without a full
        N+1 scan.
        """
        records = self._integrity.list_processed(collection=collection)
        default = collection or "default"
        return [
            (rec.collection or default, rec.file_path) for rec in records
        ]

    def list_documents_paged(
        self, *, collection: str | None = None, offset: int = 0,
        limit: int,
        status: str | None = None,
        q: str | None = None, file_type: str | None = None,
        updated_after: float | None = None, updated_before: float | None = None,
        sort: str | None = None,
        source_paths_include: list[str] | None = None,
    ) -> tuple[list[DocumentInfo], int]:
        """Server-side paginated document listing.

        Returns ``(page, total)``. Only the current page's records are
        pulled from SQLite (``LIMIT/OFFSET``); then we query chunk counts
        per page-record and image counts **in one grouped SQL statement**
        for the whole page — instead of loading every document's chunks
        and images just to render one page.

        B2.5: the ``status``/``q``/``file_type``/date-range/``sort``/
        ``source_paths_include`` predicates are forwarded to the store and
        applied in SQL — never by filtering the full listing in Python.
        """
        records = self._list_records_paged(
            collection=collection, offset=offset, limit=limit,
            status=status, q=q, file_type=file_type,
            updated_after=updated_after, updated_before=updated_before,
            sort=sort, source_paths_include=source_paths_include,
        )
        total = self._integrity_count(
            collection=collection, status=status, q=q, file_type=file_type,
            updated_after=updated_after, updated_before=updated_before,
            source_paths_include=source_paths_include,
        )
        return self._build_paged_infos(records, collection=collection), total

    def _list_records_paged(
        self, *, collection: str | None = None, offset: int = 0, limit: int,
        status: str | None = None,
        q: str | None = None, file_type: str | None = None,
        updated_after: float | None = None, updated_before: float | None = None,
        sort: str | None = None,
        source_paths_include: list[str] | None = None,
    ) -> list[Any]:
        list_method = self._integrity.list_processed
        filler = dict(
            status=status, q=q, file_type=file_type,
            updated_after=updated_after, updated_before=updated_before,
            sort=sort, source_paths_include=source_paths_include,
        )
        try:
            return list_method(
                collection=collection, limit=limit,
                offset=max(0, offset), **filler,
            )
        except TypeError:
            # Collaborators (e.g. old fakes) that don't accept the B2.5
            # filters / ``offset`` fall back to plain paging — correct,
            # just without DB-side filtering (the SQLite store honours them).
            try:
                return list_method(
                    collection=collection, limit=limit,
                    offset=max(0, offset),
                )
            except TypeError:
                rows = list_method(collection=collection, limit=None)
                start = max(0, offset)
                return rows[start:start + limit]

    def _integrity_count(
        self, *, collection: str | None = None,
        status: str | None = None,
        q: str | None = None, file_type: str | None = None,
        updated_after: float | None = None, updated_before: float | None = None,
        source_paths_include: list[str] | None = None,
    ) -> int:
        count_method = getattr(self._integrity, "count", None)
        filler = dict(
            status=status, q=q, file_type=file_type,
            updated_after=updated_after, updated_before=updated_before,
            source_paths_include=source_paths_include,
        )
        if callable(count_method):
            try:
                return int(count_method(collection=collection, **filler))
            except TypeError:
                pass
            try:
                return int(count_method(collection=collection))
            except TypeError:
                pass
        # Fall back to a full listing length (used only by collaborators
        # that don't implement the aggregate count).
        return len(self._integrity.list_processed(collection=collection))

    def _build_paged_infos(
        self, records: list[Any], *, collection: str | None = None,
    ) -> list[DocumentInfo]:
        if not records:
            return []
        # One grouped image-count query for the whole page.
        doc_hashes = {
            _image_doc_hash(rec.file_hash)
            for rec in records if rec.file_hash
        }
        image_counts = self._image_counts_for(doc_hashes, collection=collection)
        out: list[DocumentInfo] = []
        for rec in records:
            rec_collection = rec.collection or (collection or "default")
            try:
                n_chunks = len(
                    self._chroma.get_by_metadata(
                        {"source_path": rec.file_path},
                        collection=rec_collection,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "chroma lookup failed for %s: %s", rec.file_path, exc,
                )
                n_chunks = 0
            n_images = image_counts.get(
                (rec_collection, _image_doc_hash(rec.file_hash)), 0,
            )
            out.append(DocumentInfo(
                source_path=rec.file_path,
                collection=rec_collection,
                n_chunks=n_chunks,
                n_images=n_images,
                file_size=rec.file_size,
                last_modified=rec.last_modified,
                status=rec.status,
                file_hash=rec.file_hash,
                created_at=rec.created_at,
                updated_at=rec.updated_at,
            ))
        return out

    def _image_counts_for(
        self, doc_hashes: set[str], *, collection: str | None = None,
    ) -> dict[tuple[str | None, str], int]:
        bulk = getattr(self._images, "count_by_doc_hashes", None)
        if callable(bulk):
            try:
                return bulk(doc_hashes, collection=collection)
            except TypeError:
                pass
        # Fallback: one ``find_by_doc_hash`` per hash (old collaborators).
        out: dict[tuple[str | None, str], int] = {}
        for doc_hash in doc_hashes:
            try:
                hits = self._images.find_by_doc_hash(
                    doc_hash, collection=collection,
                )
                out[(collection, doc_hash)] = len(hits)
            except Exception as exc:  # noqa: BLE001
                logger.warning("image count failed for %s: %s", doc_hash, exc)
                out[(collection, doc_hash)] = 0
        return out

    def get_all_collection_stats(
        self, *, collections: list[str],
    ) -> dict[str, CollectionStats]:
        """Per-collection summary cards in one aggregated pass.

        One grouped image-count query for all collections, plus a per-
        collection document count and a cheap Chroma total. This replaces
        the previous O(documents) per-collection loop that used to fetch
        every document's images individually.
        """
        image_total = self._image_counts_for_all(collections)
        doc_counts = self._document_counts_for(collections)
        result: dict[str, CollectionStats] = {}
        for name in collections:
            try:
                chroma_stats = self._chroma.get_collection_stats(
                    collection=name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("chroma stats failed: %s", exc)
                chroma_stats = {"count": 0, "collection_name": name}
            try:
                n_images = image_total.get(name, 0)
            except Exception:  # noqa: BLE001
                n_images = 0
            result[name] = CollectionStats(
                collection=chroma_stats.get("collection_name", name),
                n_documents=doc_counts.get(name, 0),
                n_chunks=int(chroma_stats.get("count", 0) or 0),
                n_images=n_images,
            )
        return result

    def _document_counts_for(
        self, collections: list[str] | None = None,
    ) -> dict[str, int]:
        """Document counts grouped by collection (one SQL query)."""
        bulk = getattr(self._integrity, "count_by_collection", None)
        if callable(bulk):
            try:
                return bulk(collections=collections)
            except TypeError:
                pass
        # Fallback: one COUNT per collection (old collaborators).
        return {
            name: self._integrity_count(collection=name)
            for name in (collections if collections is not None else [])
        }

    def _image_counts_for_all(
        self, collections: list[str] | None = None,
    ) -> dict[str, int]:
        bulk = getattr(self._images, "image_counts", None)
        if callable(bulk):
            try:
                return bulk(collections=collections)
            except TypeError:
                pass
        # Fallback: sum per-document image counts per collection.
        totals: dict[str, int] = {}
        targets = collections if collections is not None else [
            ref.name for ref in self.list_collections()
        ]
        for name in targets:
            n = 0
            for rec in self._integrity.list_processed(collection=name):
                try:
                    n += len(self._images.find_by_doc_hash(
                        _image_doc_hash(rec.file_hash), collection=name,
                    ))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("image count failed: %s", exc)
            totals[name] = n
        return totals

    # ------------------------------------------------------------------
    # Detail
    # ------------------------------------------------------------------
    def get_document_detail(
        self, source_path: str, *, collection: str = "default",
    ) -> DocumentDetail | None:
        """
        Fetch a single document's chunks + image records.

        Returns ``None`` if the source_path has no integrity
        record in ``collection`` (i.e. it was never ingested
        *there*). Otherwise returns a :class:`DocumentDetail`
        with whatever chunks / images the stores can produce —
        empty lists are fine.

        M3: the integrity lookup + chroma lookup + image lookup
        are all scoped to ``collection`` so the same
        ``source_path`` ingested into two collections is
        reported separately per collection.
        """
        rec = self._integrity.get_record_by_path(
            source_path, collection=collection,
        )
        if rec is None:
            return None
        try:
            chunks = self._chroma.get_by_metadata(
                {"source_path": source_path},
                collection=collection,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "chroma lookup failed for %s: %s", source_path, exc,
            )
            chunks = []
        images = self._images.find_by_doc_hash(
            _image_doc_hash(rec.file_hash),
            collection=collection,
        )
        info = DocumentInfo(
            source_path=source_path,
            collection=collection,
            n_chunks=len(chunks),
            n_images=len(images),
            file_size=rec.file_size,
            last_modified=rec.last_modified,
            status=rec.status,
            file_hash=rec.file_hash,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )
        return DocumentDetail(info=info, chunks=chunks, images=images)

    # ------------------------------------------------------------------
    # Delete — cross-store coordination
    # ------------------------------------------------------------------
    def delete_document(
        self,
        source_path: str,
        *,
        collection: str = "default",
    ) -> DeleteResult:
        """
        Delete a document from all four stores (scoped to ``collection``).

        Order of operations:

        1. Resolve the integrity record → get the ``file_hash``
           (also used as image ``doc_hash``). Scoped to ``collection``.
        2. Fetch the chunk list from Chroma (we need IDs for
           BM25 removal). Scoped to ``collection``.
        3. Delete from Chroma. Scoped to ``collection``.
        4. Remove from BM25 in-memory **and** the on-disk
           ``<collection>.json`` index (persisted atomically), so the
           sparse path drops the chunk immediately.
        5. Count images by ``doc_hash`` (we don't unlink the
           files; the dashboard keeps them on disk for
           reproducibility). Scoped to ``collection``.
        6. Forget the integrity record (composite
           ``(collection, file_hash)`` PK).

        Any step that fails is recorded in ``result.errors``
        and the next step still runs. The caller decides
        whether the partial result is acceptable.
        """
        result = DeleteResult(
            source_path=source_path, collection=collection,
        )

        rec = self._integrity.get_record_by_path(
            source_path, collection=collection,
        )
        file_hash = rec.file_hash if rec is not None else None
        doc_hash = _image_doc_hash(file_hash) if file_hash is not None else None

        # Resolve chunks by both identifiers. The hash survives stale or
        # temporary source paths and prevents orphaned vectors.
        chroma_hits_by_id: dict[str, dict[str, Any]] = {}
        try:
            path_hits = self._chroma.get_by_metadata(
                {"source_path": source_path},
                collection=collection,
            )
            chroma_hits_by_id.update({hit["id"]: hit for hit in path_hits})
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"chroma.get_by_metadata: {exc}")

        if doc_hash:
            try:
                hash_hits = self._chroma.get_by_metadata(
                    {"doc_hash": doc_hash}, collection=collection,
                )
                chroma_hits_by_id.update({hit["id"]: hit for hit in hash_hits})
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"chroma.get_by_doc_hash: {exc}")

        chroma_hits = list(chroma_hits_by_id.values())
        chunk_ids = list(chroma_hits_by_id)
        if chunk_ids:
            try:
                result.chunks_deleted = self._chroma.delete(
                    chunk_ids, collection=collection,
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"chroma.delete: {exc}")

        # 2) BM25: remove the chunk_ids we just saw from BOTH the
        # in-memory index (dashboard / legacy path) and the **on-disk
        # collection index**, then persist atomically — so the Web
        # API's sparse path sees the removal immediately. (Previously
        # only the in-memory index changed; the on-disk index kept the
        # deleted chunks until the next full rebuild.)
        if chunk_ids:
            removed = 0
            if self._bm25_index is not None:
                try:
                    self._bm25_indexer.remove_documents(
                        self._bm25_index, chunk_ids,
                    )
                    removed = len(chunk_ids)
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"bm25.in_memory: {exc}")
            # Serialize with concurrent uploads / deletes on the same
            # collection so load→remove→save never drops a concurrent
            # writer's update.
            with bm25_write_lock(collection):
                try:
                    disk_index = self._bm25_indexer.load(collection)
                    self._bm25_indexer.remove_documents(
                        disk_index, chunk_ids,
                    )
                    self._bm25_indexer.save(disk_index, collection)
                    removed = len(chunk_ids)
                except (FileNotFoundError, KeyError, ValueError):
                    # No usable on-disk index for this collection yet —
                    # nothing to persist; the document is still removed
                    # from Chroma + integrity below.
                    pass
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(f"bm25.on_disk: {exc}")
            result.bm25_removed = removed

        # 3) Image storage: by doc_hash (= file_hash).
        # We count the records; we don't actually unlink the
        # files. The dashboard keeps the on-disk image bytes
        # so an undelete can recover them. If a future
        # requirement needs file deletion, add it here.
        if doc_hash:
            try:
                image_records = self._images.find_by_doc_hash(
                    doc_hash, collection=collection,
                )
                result.images_deleted = len(image_records)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"images.find_by_doc_hash: {exc}")

        # Keep the UI's system-of-record when a backing store failed, so the
        # deletion stays visible and can safely be retried.
        if file_hash and not result.errors:
            try:
                result.integrity_forgotten = self._integrity.forget(
                    file_hash, collection=collection,
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"integrity.forget: {exc}")

        return result

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def get_collection_stats(
        self, *, collection: str = "default",
    ) -> CollectionStats:
        """
        Per-collection summary card.

        ``n_documents`` is the count of distinct source_paths
        we know about in this collection (from file integrity);
        ``n_chunks`` and ``n_images`` come from the vector /
        image stores for the same collection.

        M3 update: the integrity + chroma + image counts are
        all scoped to ``collection`` (was a global view in
        v0.2).

        We don't query Chroma for a list of distinct sources
        (one ``get`` per chunk) — that's O(N) and would not
        scale. The file integrity DB is the cheaper
        system-of-record for "how many source files have we
        seen".
        """
        try:
            stats = self._chroma.get_collection_stats(collection=collection)
        except Exception as exc:  # noqa: BLE001
            logger.warning("chroma stats failed: %s", exc)
            stats = {"count": 0, "collection_name": collection}

        n_documents = self._integrity_count(collection=collection)
        n_images = self._image_counts_for_all([collection]).get(collection, 0)

        return CollectionStats(
            collection=stats.get("collection_name", collection),
            n_documents=n_documents,
            n_chunks=stats.get("count", 0),
            n_images=n_images,
        )

    def get_corpus_overview_stats(
        self, *, collections: list[str],
    ) -> CorpusOverviewStats:
        """Return overview totals without the per-document N+1 lookups."""
        allowed = set(collections)
        records = [
            record for record in self._integrity.list_processed()
            if (record.collection or "default") in allowed
        ]
        documents = {
            (record.collection or "default", record.file_path)
            for record in records
        }

        n_chunks = 0
        for collection in collections:
            try:
                stats = self._chroma.get_collection_stats(
                    collection=collection,
                )
                n_chunks += int(stats.get("count", 0) or 0)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "chroma overview stats failed for %s: %s",
                    collection,
                    exc,
                )

        return CorpusOverviewStats(
            n_documents=len(documents),
            n_chunks=n_chunks,
            statuses=tuple(record.status for record in records),
        )

    # ------------------------------------------------------------------
    # Collections (knowledge-base level)
    # ------------------------------------------------------------------
    def list_collections(self) -> list[CollectionRef]:
        """Enumerate known collection names.

        A collection is the set of BM25 index files on disk
        (``<persist_dir>/*.json``) — the same layout
        ``scripts/ingest.py`` writes. Timestamps come from the
        index file's ctime / mtime.
        """
        persist = self._bm25_persist_dir()
        if not persist.is_dir():
            return []
        out: list[CollectionRef] = []
        for path in sorted(persist.glob("*.json")):
            try:
                st = path.stat()
            except OSError:
                st = None
            out.append(CollectionRef(
                name=path.stem,
                created_at=st.st_ctime if st else None,
                updated_at=st.st_mtime if st else None,
            ))
        return out

    def create_collection(self, name: str) -> None:
        """Persist a collection marker so it shows up in listings.

        Idempotent: writes an empty BM25 index file. The pipeline later
        ingesting into the same collection overwrites it — the marker is
        only there so a freshly-created knowledge base is visible before
        any document is uploaded.
        """
        persist = self._bm25_persist_dir()
        persist.mkdir(parents=True, exist_ok=True)
        path = persist / f"{name}.json"
        if path.exists():
            return
        path.write_text(json.dumps({
            "n_docs": 0,
            "avgdl": 0.0,
            "k1": self._bm25_indexer.k1,
            "b": self._bm25_indexer.b,
            "terms": {},
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete_collection(self, name: str) -> list[DeleteResult]:
        """Cascade-delete a collection: every document + its BM25 marker.

        Returns the per-document ``DeleteResult`` list. Since M3 the
        integrity records carry a ``(collection, file_hash)`` composite
        key, so only documents ingested into ``name`` are deleted —
        other collections are untouched.
        """
        results: list[DeleteResult] = []
        for info in self.list_documents(collection=name):
            results.append(
                self.delete_document(info.source_path, collection=name),
            )
        path = self._bm25_persist_dir() / f"{name}.json"
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # noqa: BLE001
            logger.warning("failed to remove bm25 index %s: %s", path, exc)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _bm25_persist_dir(self) -> Path:
        persist = getattr(self._bm25_indexer, "persist_dir", None)
        if persist is None:
            return Path("./data/db/bm25")
        return Path(persist)


__all__ = [
    "CollectionRef",
    "DocumentManager",
    "DocumentInfo",
    "DocumentDetail",
    "DeleteResult",
    "CollectionStats",
]
