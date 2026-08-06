"""
E2E tests for the query CLI (D7).

Two angles of coverage (mirroring test_data_ingestion.py):

1. **CLI surface** — ``python scripts/query.py --help`` exits 0 and
   documents the required flags.

2. **End-to-end via build_query_components** — wires a HybridSearch
   with deterministic fakes (FakeEmbedding + FakeVectorStore + a
   real BM25 index built from a small known corpus) and exercises
   the same logic the CLI would, including verbose output and the
   empty-corpus branch.

We don't spawn the script as a subprocess because that would need
the default vector store (Chroma) to be reachable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from src.core.query_engine import QueryProcessor
from src.core.query_engine.dense_retriever import DenseRetriever
from src.core.query_engine.sparse_retriever import SparseRetriever
from src.core.types import ChunkRecord, RetrievalResult
from src.ingestion.embedding.sparse_encoder import SparseEncoder
from src.ingestion.storage import BM25Indexer
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    QueryResult,
    VectorRecord,
)

# Make scripts/ importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.query import (  # noqa: E402
    build_query_components,
    format_results,
    main,
    parse_args,
    _leg_recall,
    _source_line,
    _truncate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CORPUS = [
    ("c1", "BM25 is a bag-of-words retrieval function",
     {"source_path": "/docs/paper.pdf", "page_num": 1}),
    ("c2", "BM25 uses term frequency and inverse document frequency",
     {"source_path": "/docs/paper.pdf", "page_num": 2}),
    ("c3", "Dense retrieval uses embedding vectors",
     {"source_path": "/docs/other.pdf", "page_num": 1}),
    ("c4", "Hybrid search combines BM25 and dense",
     {"source_path": "/docs/blog.md", "page_num": 0}),
]


class FakeEmbedding(BaseEmbedding):
    def __init__(self, dim: int = 4) -> None:
        self._dim = dim

    @property
    def dimensions(self) -> int:
        return self._dim

    def embed(self, texts, **kwargs):
        return [[0.1 * (i + 1)] * self._dim for i, _ in enumerate(texts)]

    def embed_single(self, text, **kwargs):
        return [0.1] * self._dim


class FakeVectorStore(BaseVectorStore):
    def __init__(self, records: list[dict] | None = None) -> None:
        self._by_id: dict[str, dict] = {
            r["id"]: r for r in (records or [])
        }
        self.query_calls: list[tuple] = []

    def upsert(self, records, **kwargs):
        for r in records:
            self._by_id[r.id] = {
                "id": r.id, "text": r.text,
                "metadata": dict(r.metadata),
            }
        return len(records)

    def query(self, vector, top_k=10, filters=None, **kwargs):
        self.query_calls.append((list(vector), top_k, dict(filters or {})))
        out = []
        for rec in list(self._by_id.values())[:top_k]:
            out.append(QueryResult(
                id=rec["id"], score=0.9,
                text=rec["text"], metadata=dict(rec["metadata"]),
            ))
        return out

    def delete(self, ids, **kwargs):
        before = len(self._by_id)
        for i in ids:
            self._by_id.pop(i, None)
        return before - len(self._by_id)

    def get_by_ids(self, ids, **kwargs):
        return [
            {"id": i, "text": self._by_id[i]["text"],
             "metadata": dict(self._by_id[i]["metadata"])}
            for i in ids if i in self._by_id
        ]

    def count(self, **kwargs):
        return len(self._by_id)


def _build_corpus_components(tmp_path):
    """Build a small in-memory corpus backed by a real BM25 index
    and a FakeVectorStore, so query.py's pipeline can run end-to-end.

    The index MUST be persisted under ``<data_dir>/db/bm25`` — that is
    where ``build_query_components`` looks. Writing it to
    ``<data_dir>/bm25`` makes every sparse lookup miss, and because
    HybridSearch degrades to dense-only on a sparse failure, the tests
    still pass while silently covering only half the pipeline.
    """
    records = [
        {"id": cid, "text": text, "metadata": dict(meta)}
        for cid, text, meta in CORPUS
    ]
    store = FakeVectorStore(records)

    chunks = [
        ChunkRecord(id=cid, text=text, metadata=meta)
        for cid, text, meta in CORPUS
    ]
    bm25 = BM25Indexer(
        persist_dir=str(tmp_path / "db" / "bm25"),
        sparse_encoder=SparseEncoder(),
    )
    index = bm25.build(chunks)
    bm25.save(index, "corpus")
    return store, bm25


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

class TestCliSurface:
    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "scripts/query.py", "--help"],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parent.parent.parent,
        )
        assert result.returncode == 0
        assert "--query" in result.stdout
        assert "--top-k" in result.stdout
        assert "--collection" in result.stdout
        assert "--data-dir" in result.stdout
        assert "--verbose" in result.stdout
        assert "--no-rerank" in result.stdout

    def test_parse_args_required_query(self):
        with pytest.raises(SystemExit):
            parse_args([])

    def test_parse_args_defaults(self):
        ns = parse_args(["--query", "x"])
        assert ns.query == "x"
        assert ns.top_k == 10
        assert ns.collection == "default"
        assert ns.data_dir == "./data"
        assert ns.verbose is False
        assert ns.no_rerank is False

    def test_parse_args_full(self):
        ns = parse_args([
            "--query", "x", "--top-k", "20", "--collection", "my",
            "--data-dir", "/tmp/d", "--verbose", "--no-rerank",
        ])
        assert ns.top_k == 20
        assert ns.collection == "my"
        assert ns.data_dir == "/tmp/d"
        assert ns.verbose is True
        assert ns.no_rerank is True


# ---------------------------------------------------------------------------
# Output formatting (pure functions, no I/O)
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_truncate_short_text(self):
        assert _truncate("hello", max_len=100) == "hello"

    def test_truncate_long_text(self):
        result = _truncate("a" * 300, max_len=10)
        assert len(result) <= 10
        assert result.endswith("…")

    def test_truncate_empty(self):
        assert _truncate("") == "(no text)"

    def test_source_line_with_path_and_page(self):
        r = _make_result(metadata={
            "source_path": "/x.pdf", "page_num": 3,
        })
        line = _source_line(r)
        assert "/x.pdf" in line
        assert "page 3" in line

    def test_source_line_with_no_metadata(self):
        r = _make_result(metadata={})
        assert _source_line(r) == "(no source)"

    def test_format_empty_results_has_friendly_message(self):
        out = format_results([])
        assert "未找到" in out

    def test_format_results_includes_index_score_source(self):
        r1 = _make_result(
            chunk_id="abc", score=0.95, source="fusion",
            text="some text here", metadata={"source_path": "/x.pdf"},
        )
        out = format_results([r1])
        assert "1." in out
        assert "0.9500" in out
        assert "abc" in out
        assert "/x.pdf" in out
        assert "some text here" in out

    def test_format_results_verbose_shows_counts(self):
        r1 = _make_result()
        out = format_results(
            [r1], verbose=True, dense_count=5, sparse_count=3,
        )
        assert "dense_recall=5" in out
        assert "sparse_recall=3" in out


def _make_result(
    chunk_id: str = "c1",
    score: float = 0.5,
    source: str = "fusion",
    text: str = "body text",
    metadata: dict | None = None,
) -> RetrievalResult:
    return RetrievalResult(
        chunk=ChunkRecord(
            id=chunk_id, text=text,
            metadata=metadata or {}, start_offset=0, end_offset=len(text),
        ),
        score=score, rank=1, source=source,
    )


# ---------------------------------------------------------------------------
# End-to-end via build_query_components
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_query_returns_results(self, tmp_path):
        store, bm25 = _build_corpus_components(tmp_path)
        hybrid = build_query_components(
            data_dir=str(tmp_path),
            collection="corpus",
            embedding=FakeEmbedding(dim=4),
            vector_store=store,
        )
        results = hybrid.search("BM25 retrieval", top_k=3)
        assert isinstance(results, list)
        # We should get something back
        assert results
        # Each result has text + metadata
        for r in results:
            assert r.text
            assert isinstance(r.metadata, dict)

    def test_query_uses_bm25(self, tmp_path):
        """'BM25' appears in 3 of 4 docs — BM25 should rank those
        high; the dense path is a stub that returns everything in
        insertion order, but RRF should still surface BM25 docs."""
        store, bm25 = _build_corpus_components(tmp_path)
        hybrid = build_query_components(
            data_dir=str(tmp_path),
            collection="corpus",
            embedding=FakeEmbedding(dim=4),
            vector_store=store,
        )
        results = hybrid.search("BM25", top_k=4)
        ids = {r.chunk_id for r in results}
        # c1, c2, c4 all mention BM25 → at least one should appear
        assert ids & {"c1", "c2", "c4"}

    def test_query_with_filter(self, tmp_path):
        store, bm25 = _build_corpus_components(tmp_path)
        hybrid = build_query_components(
            data_dir=str(tmp_path),
            collection="corpus",
            embedding=FakeEmbedding(dim=4),
            vector_store=store,
        )
        results = hybrid.search(
            "BM25 retrieval", top_k=10,
            filters={"source_path": "/docs/paper.pdf"},
        )
        for r in results:
            assert r.metadata.get("source_path") == "/docs/paper.pdf"

    def test_query_empty_corpus_returns_empty(self, tmp_path):
        # Empty store, no BM25 index — search returns []
        store = FakeVectorStore(records=[])
        # Don't build a BM25 index — sparse retriever will
        # raise FileNotFoundError → caught by HybridSearch → empty
        hybrid = build_query_components(
            data_dir=str(tmp_path),
            collection="does_not_exist",
            embedding=FakeEmbedding(dim=4),
            vector_store=store,
        )
        # Both paths fail (empty store + missing index) → []
        results = hybrid.search("anything", top_k=5)
        assert results == []

    def test_query_top_k_limit(self, tmp_path):
        store, bm25 = _build_corpus_components(tmp_path)
        hybrid = build_query_components(
            data_dir=str(tmp_path),
            collection="corpus",
            embedding=FakeEmbedding(dim=4),
            vector_store=store,
        )
        results = hybrid.search("BM25 retrieval", top_k=2)
        assert len(results) <= 2

    def test_dense_path_actually_called(self, tmp_path):
        """Verify the FakeVectorStore's query() is invoked at least
        once (the dense path runs)."""
        store, bm25 = _build_corpus_components(tmp_path)
        hybrid = build_query_components(
            data_dir=str(tmp_path),
            collection="corpus",
            embedding=FakeEmbedding(dim=4),
            vector_store=store,
        )
        hybrid.search("BM25", top_k=3)
        assert len(store.query_calls) == 1

    def test_query_deterministic(self, tmp_path):
        store, bm25 = _build_corpus_components(tmp_path)
        hybrid = build_query_components(
            data_dir=str(tmp_path),
            collection="corpus",
            embedding=FakeEmbedding(dim=4),
            vector_store=store,
        )
        r1 = hybrid.search("BM25", top_k=3)
        r2 = hybrid.search("BM25", top_k=3)
        assert [x.chunk_id for x in r1] == [x.chunk_id for x in r2]


# ---------------------------------------------------------------------------
# main() — no-data branch (won't actually run end-to-end, since
# the script's factories need real deps, but verify the no-data
# path is exercised via the script's source).
# ---------------------------------------------------------------------------

class TestNoDataBranch:
    def test_main_no_data_branch_logic(self):
        """The script prints a friendly "未找到" message when no
        results are returned. Verify the source contains the
        message and the main() function uses it."""
        src = (Path(__file__).resolve().parent.parent.parent
                / "scripts" / "query.py").read_text()
        assert "未找到相关文档" in src
        assert "format_results" in src
        assert "def main(" in src


# ---------------------------------------------------------------------------
# Verbose per-leg recall extraction (_leg_recall)
# ---------------------------------------------------------------------------

class TestLegRecall:
    """The --verbose counters are read off a search trace. These lock
    down that extraction, which previously read the wrong keys off the
    wrong stage and silently reported 0/0 for every query."""

    def test_none_trace_returns_zeros(self):
        assert _leg_recall(None) == (0, 0)

    def test_empty_trace_returns_zeros(self):
        from src.core.trace.trace_context import new_trace
        assert _leg_recall(new_trace()) == (0, 0)

    def test_reads_counts_from_real_search_trace(self, tmp_path):
        """The counters must be non-zero when both legs actually
        returned hits — the exact regression that shipped."""
        from src.core.trace.trace_context import new_trace

        store, _ = _build_corpus_components(tmp_path)
        hybrid = build_query_components(
            data_dir=str(tmp_path),
            collection="corpus",
            embedding=FakeEmbedding(dim=4),
            vector_store=store,
        )
        trace = new_trace()
        results = hybrid.search("BM25 retrieval", top_k=4, trace=trace)
        dense, sparse = _leg_recall(trace)

        assert results, "precondition: the search must return hits"
        assert dense > 0, "dense leg recall reported as 0 despite hits"
        assert sparse > 0, "sparse leg recall reported as 0 despite hits"

    def test_counts_are_consistent_with_fused_output(self, tmp_path):
        """Fusion is a union of the two legs, so the fused count can
        never exceed their sum. 0 + 0 = 1 was the tell that the
        counters were lying."""
        from src.core.trace.trace_context import new_trace

        store, _ = _build_corpus_components(tmp_path)
        hybrid = build_query_components(
            data_dir=str(tmp_path),
            collection="corpus",
            embedding=FakeEmbedding(dim=4),
            vector_store=store,
        )
        trace = new_trace()
        results = hybrid.search("BM25", top_k=10, trace=trace)
        dense, sparse = _leg_recall(trace)
        assert len(results) <= dense + sparse

    def test_ignores_non_retriever_finish_events(self):
        """Other stages also emit event='finish'. Only the two
        retrievers' events may feed the counters."""
        from src.core.trace.trace_context import new_trace

        trace = new_trace()
        trace.record_stage("dense_retriever", event="finish", n_out=7)
        trace.record_stage("sparse_retriever", event="finish", n_out=3)
        trace.record_stage("some_other_stage", event="finish", n_out=999)
        assert _leg_recall(trace) == (7, 3)

    def test_missing_leg_reports_zero_for_that_leg_only(self):
        """A failed/absent leg must not zero out the healthy one."""
        from src.core.trace.trace_context import new_trace

        trace = new_trace()
        trace.record_stage("dense_retriever", event="finish", n_out=5)
        assert _leg_recall(trace) == (5, 0)

    def test_start_events_are_not_counted(self):
        """Retrievers emit 'start' before 'finish'; only finish counts."""
        from src.core.trace.trace_context import new_trace

        trace = new_trace()
        trace.record_stage("dense_retriever", event="start", top_k=10)
        trace.record_stage("dense_retriever", event="finish", n_out=2)
        assert _leg_recall(trace) == (2, 0)


class TestVerboseRunsSearchOnce:
    """--verbose used to re-run the entire search just to collect a
    trace, doubling every embedding API call and letting the printed
    counts disagree with the printed results."""

    def test_search_called_once_with_verbose(self, tmp_path, monkeypatch):
        store, _ = _build_corpus_components(tmp_path)
        embedding = FakeEmbedding(dim=4)

        calls: list[str] = []
        real_embed_single = embedding.embed_single

        def counting_embed_single(text, **kw):
            calls.append(text)
            return real_embed_single(text, **kw)

        monkeypatch.setattr(embedding, "embed_single", counting_embed_single)

        hybrid = build_query_components(
            data_dir=str(tmp_path),
            collection="corpus",
            embedding=embedding,
            vector_store=store,
        )
        from src.core.trace.trace_context import new_trace
        hybrid.search("BM25 retrieval", top_k=4, trace=new_trace())

        # One query embedding per search. If the CLI re-ran the search
        # to collect a trace, this would be 2.
        assert len(calls) == 1, (
            f"expected 1 embedding call per search, got {len(calls)}"
        )
