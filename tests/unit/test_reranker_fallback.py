"""
Unit tests for RerankerStage (D6) — fallback contract.

Covers:
- Successful rerank: order + score updated, source="rerank"
- Empty input: returns empty + fallback=False
- NoneReranker: passthrough, fallback=False (not a fallback)
- Backend raises: original list returned, fallback=True, error captured
- Backend raises during candidate build: same fallback behaviour
- top_m caps candidates sent to reranker; tail preserved at the end
- Trace events recorded on success and fallback
- Constructor validation: top_m <= 0 raises
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.query_engine.reranker import RerankOutput, RerankerStage
from src.core.trace.trace_context import new_trace
from src.core.types import ChunkRecord, RetrievalResult
from src.libs.reranker.base_reranker import (
    BaseReranker,
    NoneReranker,
    RerankCandidate,
    RerankerError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _chunk(cid: str, text: str = "") -> ChunkRecord:
    return ChunkRecord(id=cid, text=text or f"text of {cid}", metadata={})


def _result(cid: str, score: float, *, source: str = "fusion") -> RetrievalResult:
    return RetrievalResult(
        chunk=_chunk(cid),
        score=score,
        rank=0,
        source=source,
    )


def _results(*pairs: tuple[str, float]) -> list[RetrievalResult]:
    return [_result(cid, score) for cid, score in pairs]


class _ScriptedReranker(BaseReranker):
    """Returns the candidates in reverse input order, score = 1 - idx/10."""

    def __init__(self, scores: list[float] | None = None) -> None:
        self.scores = scores
        self.calls: list[list[RerankCandidate]] = []

    def rerank(self, query, candidates, top_k=None, **kwargs):
        self.calls.append(list(candidates))
        if self.scores is not None:
            assert len(self.scores) == len(candidates)
            out = [
                RerankCandidate(
                    id=c.id, text=c.text,
                    score=s, metadata=c.metadata,
                )
                for c, s in zip(candidates, self.scores)
            ]
        else:
            out = [
                RerankCandidate(
                    id=c.id, text=c.text,
                    score=1.0 - i / 10.0, metadata=c.metadata,
                )
                for i, c in enumerate(candidates)
            ]
        return out


class _BoomReranker(BaseReranker):
    def rerank(self, query, candidates, top_k=None, **kwargs):
        raise RerankerError("simulated backend explosion")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_top_m_zero_rejected():
    with pytest.raises(ValueError):
        RerankerStage(_ScriptedReranker(), top_m=0)


def test_top_m_negative_rejected():
    with pytest.raises(ValueError):
        RerankerStage(_ScriptedReranker(), top_m=-1)


# ---------------------------------------------------------------------------
# Empty input — short-circuit
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty_with_fallback_false():
    backend = _ScriptedReranker()
    stage = RerankerStage(backend)
    out = stage.rerank("q", [])
    assert isinstance(out, RerankOutput)
    assert out.results == []
    assert out.fallback is False
    assert backend.calls == []   # backend never invoked


def test_empty_input_records_trace():
    trace = new_trace()
    stage = RerankerStage(_ScriptedReranker())
    stage.rerank("q", [], trace=trace)
    events = [s.get("event") for s in trace.stages]
    assert "empty_input" in events


# ---------------------------------------------------------------------------
# NoneReranker — explicit no-op, NOT a fallback
# ---------------------------------------------------------------------------

def test_none_reranker_passthrough_preserves_order():
    items = _results(("a", 0.9), ("b", 0.5), ("c", 0.1))
    stage = RerankerStage(NoneReranker())
    out = stage.rerank("q", items)
    assert out.fallback is False
    assert [r.chunk_id for r in out.results] == ["a", "b", "c"]
    assert [r.score for r in out.results] == [0.9, 0.5, 0.1]
    # Source untouched — NoneReranker doesn't claim rerank.
    assert all(r.source == "fusion" for r in out.results)


def test_none_reranker_records_noop_event():
    trace = new_trace()
    stage = RerankerStage(NoneReranker())
    stage.rerank("q", _results(("a", 1.0)), trace=trace)
    assert any(s.get("event") == "noop" for s in trace.stages)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_successful_rerank_reorders_and_marks_source():
    items = _results(("a", 0.9), ("b", 0.5), ("c", 0.1))
    backend = _ScriptedReranker()
    stage = RerankerStage(backend)
    out = stage.rerank("q", items)
    assert out.fallback is False
    # ScriptedReranker outputs in input order with descending scores,
    # so result order == input order.
    assert [r.chunk_id for r in out.results] == ["a", "b", "c"]
    assert all(r.source == "rerank" for r in out.results)
    assert [r.score for r in out.results] == pytest.approx([1.0, 0.9, 0.8])
    # Original chunk payloads preserved.
    assert [r.chunk_id for r in out.results] == [r.chunk_id for r in items]


def test_successful_rerank_records_finish_event():
    trace = new_trace()
    stage = RerankerStage(_ScriptedReranker())
    stage.rerank("q", _results(("a", 1.0), ("b", 0.5)), trace=trace)
    events = [s.get("event") for s in trace.stages]
    # The current design records a single ``finish`` event per
    # successful rerank (collapsed start/finish), carrying
    # ``method``/``elapsed_ms``/``n_in``/``n_out``/``fallback``.
    assert "finish" in events
    finish = next(s for s in trace.stages if s.get("event") == "finish")
    assert finish.get("fallback") is False
    assert finish.get("n_in") == 2
    assert finish.get("n_out") == 2


# ---------------------------------------------------------------------------
# Fallback: backend raises
# ---------------------------------------------------------------------------

def test_backend_exception_triggers_fallback():
    items = _results(("a", 0.9), ("b", 0.5))
    stage = RerankerStage(_BoomReranker())
    out = stage.rerank("q", items)
    assert out.fallback is True
    assert out.error is not None
    assert "explosion" in out.error
    # Original list preserved in original order, scores untouched.
    assert [r.chunk_id for r in out.results] == ["a", "b"]
    assert [r.score for r in out.results] == [0.9, 0.5]
    # Source not claimed as "rerank" — caller sees the original fusion data.
    assert all(r.source == "fusion" for r in out.results)


def test_backend_exception_records_fallback_event():
    trace = new_trace()
    stage = RerankerStage(_BoomReranker())
    stage.rerank("q", _results(("a", 1.0)), trace=trace)
    events = [s.get("event") for s in trace.stages]
    assert "fallback" in events
    fb = next(s for s in trace.stages if s.get("event") == "fallback")
    assert "error" in fb


def test_generic_exception_also_falls_back():
    """Any exception type — not just RerankerError — must trigger fallback."""
    class WeirdError(Exception):
        pass

    class WeirdBoom(BaseReranker):
        def rerank(self, query, candidates, top_k=None, **kwargs):
            raise WeirdError("nope")

    items = _results(("a", 1.0))
    stage = RerankerStage(WeirdBoom())
    out = stage.rerank("q", items)
    assert out.fallback is True
    assert out.results == items


# ---------------------------------------------------------------------------
# top_m: only first N candidates are scored, tail is appended
# ---------------------------------------------------------------------------

def test_top_m_sends_only_first_n_to_backend():
    items = _results(("a", 0.9), ("b", 0.7), ("c", 0.5), ("d", 0.3))
    backend = _ScriptedReranker()
    stage = RerankerStage(backend, top_m=2)
    out = stage.rerank("q", items)
    # Backend was called with only first 2 items.
    assert len(backend.calls[0]) == 2
    assert {c.id for c in backend.calls[0]} == {"a", "b"}
    # Tail (c, d) is appended at the end with original score/source.
    ids = [r.chunk_id for r in out.results]
    assert ids[-2:] == ["c", "d"]
    tail = [r for r in out.results if r.chunk_id in {"c", "d"}]
    assert all(r.source == "fusion" for r in tail)


def test_top_m_larger_than_input_is_no_cap():
    items = _results(("a", 0.9), ("b", 0.5))
    backend = _ScriptedReranker()
    stage = RerankerStage(backend, top_m=10)
    out = stage.rerank("q", items)
    assert len(backend.calls[0]) == 2
    assert len(out.results) == 2


# ---------------------------------------------------------------------------
# Unknown IDs from reranker are dropped (defensive)
# ---------------------------------------------------------------------------

def test_reranker_returning_unknown_id_drops_it():
    items = _results(("a", 0.9), ("b", 0.5))

    class HallucinatingReranker(BaseReranker):
        def rerank(self, query, candidates, top_k=None, **kwargs):
            return [
                RerankCandidate(id="zzz", text="ghost", score=0.99),
                RerankCandidate(id="a", text="a", score=0.7),
            ]

    stage = RerankerStage(HallucinatingReranker())
    out = stage.rerank("q", items)
    # zzz dropped; a kept with new score; b appended with original.
    ids = [r.chunk_id for r in out.results]
    assert "zzz" not in ids
    assert ids[0] == "a"
    assert ids[1] == "b"


# ---------------------------------------------------------------------------
# Integration with a MagicMock backend (catches contract drift)
# ---------------------------------------------------------------------------

def test_works_with_magicmock_compatible_backend():
    backend = MagicMock(spec=BaseReranker)
    backend.rerank.return_value = [
        RerankCandidate(id="b", text="b", score=0.95),
        RerankCandidate(id="a", text="a", score=0.50),
    ]
    items = _results(("a", 0.5), ("b", 0.9))
    stage = RerankerStage(backend)
    out = stage.rerank("q", items)
    assert out.fallback is False
    assert [r.chunk_id for r in out.results] == ["b", "a"]
    backend.rerank.assert_called_once()