"""
Integration tests for the deterministic Golden Set seed (Task 01.3).

Builds the REAL production retrieval stack in temporary data directories
(real Chroma persistent store + real BM25 JSON index via production
classes) and verifies:

* indexes and the manifest are created with the authored corpus content;
* rebuilding is byte-deterministic and refuses non-empty directories;
* every answerable golden case's labelled target is retrievable in the
  top-5 through the application QueryService (hybrid AND sparse), and
  metadata filters work;
* the no-answer case yields zero BM25 hits.
"""

from __future__ import annotations

import json

import pytest

from scripts.eval_support import (
    CASES_PATH,
    CORPUS_PATH,
    DeterministicHashEmbedding,
    GOLDEN_COLLECTION,
    MANIFEST_NAME,
    corpus_chunks,
    load_corpus,
    seed_fixture_data,
)
from scripts.query import build_query_components
from src.application.services import QueryService
from src.core.settings import VectorStoreSettings
from src.libs.vector_store.chroma_store import ChromaStore


def _load_cases() -> list[dict]:
    cases = []
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


@pytest.fixture(scope="module")
def seeded_dir(tmp_path_factory) -> "pytest.TempPathFactory":
    path = tmp_path_factory.mktemp("golden")
    seed_fixture_data(path, corpus_path=CORPUS_PATH)
    return path


@pytest.fixture(scope="module")
def query_service(seeded_dir) -> QueryService:
    store = ChromaStore(
        VectorStoreSettings(
            backend="chroma",
            persist_path=str(seeded_dir / "db" / "chroma"),
            collection_name=GOLDEN_COLLECTION,
        ),
    )
    hybrid = build_query_components(
        data_dir=str(seeded_dir),
        collection=GOLDEN_COLLECTION,
        embedding=DeterministicHashEmbedding(),
        vector_store=store,
    )
    return QueryService(hybrid)


def test_seed_creates_chroma_bm25_and_manifest(seeded_dir):
    assert (seeded_dir / MANIFEST_NAME).is_file()
    assert (seeded_dir / "db" / "bm25" / f"{GOLDEN_COLLECTION}.json").is_file()
    assert (seeded_dir / "db" / "chroma").is_dir()

    manifest = json.loads((seeded_dir / MANIFEST_NAME).read_text("utf-8"))
    corpus = load_corpus(CORPUS_PATH)
    golden = corpus_chunks(corpus)
    assert manifest["chunk_count"] == len(golden) == 18
    assert manifest["document_count"] == len(corpus["documents"]) == 7
    assert manifest["embedding_profile"] == "deterministic-hash-v1"

    store = ChromaStore(
        VectorStoreSettings(
            backend="chroma",
            persist_path=str(seeded_dir / "db" / "chroma"),
            collection_name=GOLDEN_COLLECTION,
        ),
    )
    assert store.count() == 18
    stored = store.get_by_ids([c["chunk_id"] for c in manifest["chunks"]])
    assert {r["id"] for r in stored} == {c["chunk_id"] for c in manifest["chunks"]}


def test_seed_refuses_nonempty_dir_without_force(tmp_path):
    seed_fixture_data(tmp_path)
    (tmp_path / "sentinel").write_text("x")
    with pytest.raises(FileExistsError):
        seed_fixture_data(tmp_path)
    # force=True rebuilds fine.
    seed_fixture_data(tmp_path, force=True)


def test_manifest_and_bm25_byte_deterministic(tmp_path_factory):
    first = tmp_path_factory.mktemp("a")
    second = tmp_path_factory.mktemp("b")
    seed_fixture_data(first)
    seed_fixture_data(second)

    assert (first / MANIFEST_NAME).read_bytes() == (
        second / MANIFEST_NAME
    ).read_bytes()
    bm25_name = f"{GOLDEN_COLLECTION}.json"
    assert (first / "db" / "bm25" / bm25_name).read_bytes() == (
        second / "db" / "bm25" / bm25_name
    ).read_bytes()


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_answerable_targets_in_top5(query_service, case):
    if case["category"] == "no_answer":
        pytest.skip("no-answer case has its own assertion")
    for mode in ("hybrid", "sparse"):
        result = query_service.search(
            case["query"], mode=mode, top_k=5, filters=case["filters"] or None,
        )
        ids = [c.chunk_id for c in result.chunks]
        assert set(case["relevant_chunk_ids"]) & set(ids), (
            f"{case['id']} ({mode}): target not in top-5: {ids}"
        )


def test_no_answer_case_empty_bm25(query_service):
    case = next(c for c in _load_cases() if c["category"] == "no_answer")
    sparse = query_service.search(case["query"], mode="sparse", top_k=10)
    assert sparse.chunks == []


def test_pdf_filter_restricts_to_pdf_documents(query_service):
    case = next(c for c in _load_cases() if c["id"] == "xk2200-ip65-multicolumn")
    result = query_service.search(
        case["query"], mode="hybrid", top_k=10, filters=case["filters"],
    )
    assert result.chunks, "filtered query must return results"
    assert all(c.metadata.get("doc_type") == "pdf" for c in result.chunks)
    assert case["relevant_chunk_ids"][0] in [c.chunk_id for c in result.chunks]


def test_repeated_search_returns_identical_ids(query_service):
    case = next(c for c in _load_cases() if c["id"] == "sales-south-q2-table")
    first = [c.chunk_id for c in query_service.search(case["query"], top_k=10).chunks]
    second = [c.chunk_id for c in query_service.search(case["query"], top_k=10).chunks]
    assert first == second
