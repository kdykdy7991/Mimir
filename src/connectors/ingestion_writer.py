"""Concrete whole-document sync writer over the existing ingestion pipeline."""

from __future__ import annotations

import hashlib
import time
from uuid import UUID

from src.application.identifiers import collection_uuid, document_uuid
from src.application.services.document_service import DocumentService
from src.application.services.ingestion_service import IngestionService
from src.connectors.contracts import SourceDocument
from src.connectors.store import DataSource
from src.ingestion.storage.parent_chunk_store import ParentChunkStore


class SyncIngestionError(RuntimeError):
    code = "sync_ingestion_failed"


class SyncIngestionTimeout(SyncIngestionError):
    code = "sync_ingestion_timeout"


class IngestionDocumentWriter:
    """Wait for the real parse→index task before acknowledging a revision."""

    _EXTENSIONS = {
        "application/pdf": ".pdf", "text/markdown": ".md",
        "text/plain": ".txt", "text/html": ".html",
        "application/epub+zip": ".epub", "image/png": ".png",
        "image/jpeg": ".jpg",
    }

    def __init__(
        self, ingestion: IngestionService, documents: DocumentService,
        parent_chunks: ParentChunkStore, *, timeout_seconds: float = 300.0,
    ) -> None:
        self.ingestion = ingestion
        self.documents = documents
        self.parent_chunks = parent_chunks
        self.timeout_seconds = timeout_seconds

    def document_identity(self, source: DataSource, document: SourceDocument) -> str:
        collection = self._collection_name(source.collection_id)
        path = self.ingestion.compute_source_path(collection, self._filename(source, document))
        return str(document_uuid(collection, str(path)))

    def resolve_current(
        self, source: DataSource, document: SourceDocument,
    ) -> tuple[str, str] | None:
        document_id = self.document_identity(source, document)
        collection = self._collection_name(source.collection_id)
        version = self.parent_chunks.active_version(collection, document_id)
        return (document_id, version) if version else None

    def __call__(
        self, source: DataSource, expected_document_id: str,
        document: SourceDocument,
    ) -> str:
        collection = self._collection_name(source.collection_id)
        filename = self._filename(source, document)
        source_path = self.ingestion.compute_source_path(collection, filename)
        actual_document_id = str(document_uuid(collection, str(source_path)))
        if actual_document_id != expected_document_id:
            raise SyncIngestionError("sync document identity mismatch")
        if document.deleted:
            self.documents.delete_document(str(source_path), collection)
            return f"deleted:{document.revision}"
        record = self.ingestion.upload(
            bytes_payload=document.content, filename=filename,
            collection=collection, collection_id=collection_uuid(collection),
            document_id=UUID(actual_document_id), source_path=source_path,
        )
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            current = self.ingestion.get_task(record.id)
            if current is not None and current.status in {"succeeded", "skipped"}:
                version = self.parent_chunks.active_version(collection, actual_document_id)
                if not version:
                    raise SyncIngestionError("ingestion completed without active document version")
                return version
            if current is not None and current.status in {"failed", "cancelled"}:
                raise SyncIngestionError("sync ingestion task did not succeed")
            time.sleep(0.05)
        raise SyncIngestionTimeout("sync ingestion task timed out")

    def _collection_name(self, collection_id: str) -> str:
        try:
            expected = UUID(collection_id)
        except ValueError as exc:
            raise SyncIngestionError("invalid datasource collection id") from exc
        for ref in self.documents.list_collections():
            if collection_uuid(ref.name) == expected:
                return ref.name
        raise SyncIngestionError("datasource collection does not exist")

    def _filename(self, source: DataSource, document: SourceDocument) -> str:
        extension = self._EXTENSIONS.get(document.media_type)
        if extension is None:
            raise SyncIngestionError("unsupported connector media type")
        digest = hashlib.sha256(
            f"{source.id}\0{document.external_id}".encode(),
        ).hexdigest()[:32]
        return f"sync-{digest}{extension}"


__all__ = ["IngestionDocumentWriter", "SyncIngestionError", "SyncIngestionTimeout"]
