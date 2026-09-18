from src.application.contracts import ChunkRevision, DerivedKind, RevisionSource
from src.application.services.derived_content_service import DerivedContentService
from src.ingestion.storage import DerivedContentStore


def _revision(text="source truth"):
    return ChunkRevision(
        revision_id="r1", collection="kb", document_id="doc", chunk_id="chunk",
        text=text, source=RevisionSource.INITIAL, reason="initial", actor="system",
    )


def test_rebuild_creates_separate_summary_and_question_namespace(tmp_path):
    store = DerivedContentStore(tmp_path / "derived.sqlite3")
    service = DerivedContentService(
        store, lambda text: {
            "summary": "A short summary",
            "questions": ["What is true?", "What is true?", "Why?"],
        }, model="m", prompt_version="derived-v1", enabled=True,
    )
    rows = service.rebuild(_revision())
    assert [row.kind for row in rows].count(DerivedKind.SUMMARY) == 1
    assert [row.kind for row in rows].count(DerivedKind.SYNTHETIC_QUESTION) == 2
    question = next(row for row in rows if row.kind is DerivedKind.SYNTHETIC_QUESTION)
    assert question.weight == 0.7
    assert service.citation_for(question) == {
        "document_id": "doc", "chunk_id": "chunk", "revision_id": "r1",
    }


def test_rebuild_replaces_old_artifacts_and_ids_are_deterministic(tmp_path):
    store = DerivedContentStore(tmp_path / "derived.sqlite3")
    payload = {"summary": "S", "questions": ["Q?"]}
    service = DerivedContentService(
        store, lambda text: payload,
        model="m", prompt_version="p", enabled=True,
    )
    first = service.rebuild(_revision())
    second = service.rebuild(_revision())
    assert [row.artifact_id for row in first] == [row.artifact_id for row in second]
    assert len(store.list("r1")) == 2
    payload["questions"] = ["New?"]
    service.rebuild(_revision())
    assert {row.text for row in store.list("r1")} == {"S", "New?"}


def test_disable_delete_and_model_failure_are_safe(tmp_path):
    store = DerivedContentStore(tmp_path / "derived.sqlite3")
    service = DerivedContentService(
        store, lambda text: {"summary": "S", "questions": ["Q?"]},
        model="m", prompt_version="p", enabled=True,
    )
    rows = service.rebuild(_revision())
    store.set_enabled(rows[1].artifact_id, False)
    assert len(store.list("r1", enabled_only=True)) == 1
    broken = DerivedContentService(
        store, lambda text: (_ for _ in ()).throw(RuntimeError("offline")),
        model="m", prompt_version="p", enabled=True,
    )
    assert len(broken.rebuild(_revision())) == 2
    disabled = DerivedContentService(
        store, lambda text: {}, model="m", prompt_version="p", enabled=False,
    )
    assert disabled.rebuild(_revision()) == ()
    assert store.list("r1") == []
    assert store.delete_revision("r1") == 0
