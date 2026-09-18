from pathlib import Path
from types import SimpleNamespace

import pytest

from src.application.identifiers import collection_uuid
from src.connectors.contracts import SourceDocument
from src.connectors.ingestion_writer import IngestionDocumentWriter, SyncIngestionError


class FakeIngestion:
    def __init__(self, status: str = "succeeded") -> None:
        self.status = status
        self.uploads = []

    @staticmethod
    def compute_source_path(collection: str, filename: str) -> Path:
        return Path("uploads") / collection / filename

    def upload(self, **kwargs):
        self.uploads.append(kwargs)
        return SimpleNamespace(id="task-1")

    def get_task(self, task_id: str):
        assert task_id == "task-1"
        return SimpleNamespace(status=self.status)


class FakeDocuments:
    def __init__(self) -> None:
        self.deleted = []

    @staticmethod
    def list_collections():
        return [SimpleNamespace(name="manuals")]

    def delete_document(self, source_path: str, collection: str) -> None:
        self.deleted.append((source_path, collection))


class FakeParentChunks:
    def __init__(self, version: str | None = "active-v2") -> None:
        self.version = version
        self.lookups = []

    def active_version(self, collection: str, document_id: str) -> str | None:
        self.lookups.append((collection, document_id))
        return self.version


def source():
    return SimpleNamespace(id="source-1", collection_id=str(collection_uuid("manuals")))


def document(*, deleted: bool = False) -> SourceDocument:
    return SourceDocument(
        external_id="remote/article-1", revision="remote-r2", title="Article",
        content=b"updated body", media_type="text/plain",
        source_uri="https://public.example/article-1",
        checksum_sha256="sha256", deleted=deleted,
    )


def test_writer_waits_for_ingestion_and_returns_active_document_version() -> None:
    ingestion = FakeIngestion()
    parents = FakeParentChunks()
    writer = IngestionDocumentWriter(ingestion, FakeDocuments(), parents)
    item = document()
    document_id = writer.document_identity(source(), item)

    assert writer(source(), document_id, item) == "active-v2"
    assert ingestion.uploads[0]["bytes_payload"] == b"updated body"
    assert ingestion.uploads[0]["collection"] == "manuals"
    assert str(ingestion.uploads[0]["document_id"]) == document_id
    assert parents.lookups == [("manuals", document_id)]
    assert writer.resolve_current(source(), item) == (document_id, "active-v2")


def test_writer_does_not_acknowledge_failed_ingestion() -> None:
    writer = IngestionDocumentWriter(
        FakeIngestion(status="failed"), FakeDocuments(), FakeParentChunks(),
    )
    item = document()

    with pytest.raises(SyncIngestionError, match="did not succeed"):
        writer(source(), writer.document_identity(source(), item), item)


def test_writer_deletes_through_document_service_without_upload() -> None:
    ingestion = FakeIngestion()
    documents = FakeDocuments()
    writer = IngestionDocumentWriter(ingestion, documents, FakeParentChunks())
    item = document(deleted=True)

    revision = writer(source(), writer.document_identity(source(), item), item)

    assert revision == "deleted:remote-r2"
    assert ingestion.uploads == []
    assert len(documents.deleted) == 1
    assert documents.deleted[0][1] == "manuals"
    assert documents.deleted[0][0].startswith("uploads/manuals/sync-")
