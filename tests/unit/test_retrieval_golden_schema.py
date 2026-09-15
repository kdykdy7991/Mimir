"""
Offline validation of the Retrieval Golden Set v1 (Task 01.3).

These tests never instantiate Chroma or BM25 — they validate the
hand-authored artefacts themselves:

* ``cases.jsonl`` records satisfy ``schema.json``;
* every declared relevant document/chunk id is derivable from the
  authored corpus by the PRODUCTION id functions (no parallel id
  scheme, no hand-invented ids);
* the six required categories are represented;
* acceptable source locations reference real corpus documents.

The build itself (real indexes, determinism) is covered in
``tests/integration/test_retrieval_golden_seed.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.eval_support import (
    CASES_PATH,
    CASE_SCHEMA_PATH,
    CORPUS_PATH,
    GOLDEN_COLLECTION,
    CASES_SCHEMA_VERSION,
    corpus_chunks,
    document_uuid,
    load_corpus,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval_golden"

REQUIRED_CATEGORIES = {
    "exact_keyword",
    "chinese_semantic",
    "multicolumn_pdf",
    "table",
    "image_ocr",
    "no_answer",
}


@pytest.fixture(scope="module")
def corpus() -> dict:
    return load_corpus(CORPUS_PATH)


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    records = []
    for line_no, line in enumerate(CASES_PATH.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = json.loads(CASE_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def test_corpus_is_declared_synthetic_v1(corpus):
    assert corpus["schema_version"] == "golden-corpus-v1"
    assert corpus["collection"] == GOLDEN_COLLECTION
    assert corpus["synthetic"] is True
    assert corpus["documents"], "corpus must not be empty"


def test_every_case_validates_against_schema(cases, validator):
    errors = []
    for case in cases:
        for error in validator.iter_errors(case):
            errors.append(f"{case.get('id', '?')}: {error.message}")
    assert not errors, "\n".join(errors)


def test_case_ids_unique(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_all_six_required_categories_present(cases):
    assert REQUIRED_CATEGORIES <= {c["category"] for c in cases}


def test_schema_version_is_required_and_pinned(cases):
    assert all(c["schema_version"] == CASES_SCHEMA_VERSION for c in cases)


def test_exactly_one_no_answer_case_with_empty_relevance(cases):
    no_answer = [c for c in cases if c["category"] == "no_answer"]
    assert len(no_answer) == 1
    case = no_answer[0]
    assert case["expect_no_answer"] is True
    assert case["relevant_chunk_ids"] == []
    assert case["relevant_document_ids"] == []
    assert case["acceptable_source_locations"] == []


def test_answerable_cases_have_nonempty_relevance(cases):
    answerable = [c for c in cases if c["category"] != "no_answer"]
    assert len(answerable) >= 6
    for case in answerable:
        assert case["relevant_chunk_ids"], case["id"]
        assert case["relevant_document_ids"], case["id"]
        assert case["acceptable_source_locations"], case["id"]
        assert not case.get("expect_no_answer", False)


def test_relevant_ids_are_production_derivable_from_corpus(corpus, cases):
    """The frozen ids in cases.jsonl must equal the production id
    formulas applied to the authored text — copy/pasted or invented ids
    fail here when corpus text changes."""
    golden = corpus_chunks(corpus)
    from src.ingestion.chunking.document_chunker import DocumentChunker

    chunk_ids = {
        DocumentChunker._generate_chunk_id(c.document_id, c.chunk_index, c.text): c
        for c in golden
    }
    doc_paths = {d["source_path"] for d in corpus["documents"]}

    for case in cases:
        if case["category"] == "no_answer":
            continue
        loc_paths = {loc["source_path"] for loc in case["acceptable_source_locations"]}

        # Declared chunk ids must be production-derivable from authored text.
        for chunk_id in case["relevant_chunk_ids"]:
            assert chunk_id in chunk_ids, f"{case['id']}: unknown chunk id {chunk_id}"
            assert chunk_ids[chunk_id].source_path in loc_paths, (
                f"{case['id']}: relevant chunk outside acceptable_source_locations"
            )
            assert chunk_ids[chunk_id].document_id in case["relevant_document_ids"]

        # Source locations must reference real corpus documents.
        for source_path in loc_paths:
            assert source_path in doc_paths, (
                f"{case['id']}: source path not in corpus: {source_path}"
            )

        # Declared document ids must equal the production uuid5 derivation
        # (compared as sets — order is irrelevant).
        expected_doc_ids = {
            str(document_uuid(GOLDEN_COLLECTION, p)) for p in loc_paths
        }
        assert set(case["relevant_document_ids"]) == expected_doc_ids, (
            f"{case['id']}: relevant_document_ids diverge from document_uuid()"
        )


def test_chunk_refs_and_indexes_stable(corpus):
    seen_refs: set[str] = set()
    for document in corpus["documents"]:
        indexes = [c["index"] for c in document["chunks"]]
        assert indexes == list(range(len(indexes))), document["source_path"]
        for chunk in document["chunks"]:
            assert chunk["ref"] not in seen_refs
            seen_refs.add(chunk["ref"])
            assert chunk["text"].strip()
    assert len(seen_refs) == 18  # current corpus size; update deliberately


def test_multicolumn_table_ocr_chunks_carry_content_markers(corpus):
    by_ref = {
        c["ref"]: c
        for d in corpus["documents"] for c in d["chunks"]
    }
    assert by_ref["xk2200-ip-rating"]["content_type"] == "multicolumn_text"
    assert by_ref["sales-south-east"]["content_type"] == "table_text"
    assert by_ref["ocr-nameplate"]["content_type"] == "ocr_text"
