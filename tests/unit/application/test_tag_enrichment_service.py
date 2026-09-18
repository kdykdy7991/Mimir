from src.application.contracts import SuggestionStatus
from src.application.services.tag_enrichment_service import TagEnrichmentService
from src.ingestion.storage import EnrichmentStore


def test_suggestions_prefer_vocabulary_and_keep_new_names_as_candidates(tmp_path):
    store = EnrichmentStore(tmp_path / "enrichment.sqlite3")
    service = TagEnrichmentService(
        store,
        lambda text, vocab: [
            {"name": " Finance ", "confidence": 0.9},
            {"name": "Emerging Topic", "confidence": 0.7},
        ],
        model="model-a", prompt_version="tag-v1", enabled=True,
    )
    rows = service.suggest(
        collection="kb", document_id="doc", revision_id="r1", text="body",
        vocabulary=[("tag-fin", "Finance")],
    )
    assert rows[0].existing_tag_id == "tag-fin"
    assert rows[0].suggested_name == "Finance"
    assert rows[1].existing_tag_id is None
    assert all(row.status is SuggestionStatus.PENDING for row in rows)
    assert rows[0].model == "model-a" and rows[0].prompt_version == "tag-v1"


def test_manual_tags_are_protected_and_generation_failure_degrades(tmp_path):
    store = EnrichmentStore(tmp_path / "enrichment.sqlite3")
    service = TagEnrichmentService(
        store, lambda text, vocab: [{"name": "Finance", "confidence": 1.0}],
        model="m", prompt_version="p", enabled=True,
    )
    assert service.suggest(
        collection="kb", document_id="doc", revision_id="r1", text="x",
        vocabulary=[("manual", "Finance")], manual_tag_ids=["manual"],
    ) == ()
    broken = TagEnrichmentService(
        store, lambda *_: (_ for _ in ()).throw(RuntimeError("offline")),
        model="m", prompt_version="p", enabled=True,
    )
    assert broken.suggest(
        collection="kb", document_id="doc", revision_id="r1", text="x",
        vocabulary=[],
    ) == ()


def test_review_requires_existing_tag_and_applies_additively(tmp_path):
    store = EnrichmentStore(tmp_path / "enrichment.sqlite3")
    service = TagEnrichmentService(
        store, lambda *_: [{"name": "Finance", "confidence": 0.8}],
        model="m", prompt_version="p", enabled=True,
    )
    suggestion = service.suggest(
        collection="kb", document_id="doc", revision_id="r1", text="x",
        vocabulary=[("tag-fin", "Finance")],
    )[0]
    applied = []
    reviewed = service.review(
        suggestion.suggestion_id, approve=True, reviewer="reviewer:1",
        apply_existing_tag=lambda doc, tag: applied.append((doc, tag)),
    )
    assert reviewed.status is SuggestionStatus.APPROVED
    assert reviewed.reviewer == "reviewer:1"
    assert applied == [("doc", "tag-fin")]


def test_disabled_is_noop_and_duplicate_generation_is_idempotent(tmp_path):
    store = EnrichmentStore(tmp_path / "enrichment.sqlite3")
    disabled = TagEnrichmentService(
        store, lambda *_: [], model="m", prompt_version="p", enabled=False,
    )
    assert disabled.suggest(
        collection="kb", document_id="doc", revision_id="r1", text="x",
        vocabulary=[],
    ) == ()
    enabled = TagEnrichmentService(
        store, lambda *_: [{"name": "A", "confidence": 0.5}],
        model="m", prompt_version="p", enabled=True,
    )
    first = enabled.suggest(
        collection="kb", document_id="doc", revision_id="r1", text="x",
        vocabulary=[],
    )
    second = enabled.suggest(
        collection="kb", document_id="doc", revision_id="r1", text="x",
        vocabulary=[],
    )
    assert first[0].suggestion_id == second[0].suggestion_id
    assert len(store.list("doc", "r1")) == 1
