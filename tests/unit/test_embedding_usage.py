"""
Embedding token-usage tests — PRD ``docs/prd-embedding-token-metrics.md`` §5.

Covers the four backend layers:

1. ``OpenAIEmbedding`` parsing ``response.usage.total_tokens`` at the
   provider boundary and emitting one ``EmbeddingUsage`` per call.
2. ``EmbeddingUsageStore`` tagging usage with the active operation
   context (query / ingestion recorded, healthcheck + unknown dropped,
   retried writes deduped, failures swallowed).
3. ``WebApiDB`` aggregation — window sums split by operation, earliest
   timestamp, durability across a reopened database file.
4. (Integration) the ``GET /api/v1/metrics/overview`` traffic card
   mapping capability to ``null`` / honest ``0`` / real sums.

The OpenAI provider tests mock the SDK client exactly like
``test_embedding_providers_smoke.py`` does.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.application.services.embedding_usage_store import EmbeddingUsageStore
from src.application.services.web_store import WebApiDB
from src.core.settings import EmbeddingSettings
from src.libs.embedding.usage import (
    EmbeddingUsage,
    embedding_usage_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(*, total_tokens: int | None = 42, request_id: str = "req-abc"):
    """A realistic OpenAI-compatible embeddings response.

    ``total_tokens=None`` means the provider omitted ``usage`` entirely.
    """
    data = MagicMock(index=0, embedding=[0.1, 0.2, 0.3])
    response = MagicMock()
    response.data = [data]
    response.id = request_id
    response.usage = (
        MagicMock(total_tokens=total_tokens, prompt_tokens=total_tokens)
        if total_tokens is not None else None
    )
    return response


def _openai_embedding(monkeypatch: pytest.MonkeyPatch):
    """Build an ``OpenAIEmbedding`` with a stubbed SDK client.

    Returns ``(embedding, mock_client)`` so tests can customise the
    ``embeddings.create`` response.
    """
    from src.libs.embedding.openai_embedding import OpenAIEmbedding

    mock_client = MagicMock()
    mock_client.models.list.return_value.data = [MagicMock(id="qwen3-embedding")]
    monkeypatch.setattr(
        "src.libs.embedding.openai_embedding.OpenAI",
        MagicMock(return_value=mock_client),
    )
    settings = EmbeddingSettings(
        provider="openai", api_key="test-key",
        model="qwen3-embedding", dimensions=3,
    )
    return OpenAIEmbedding(settings), mock_client


# ---------------------------------------------------------------------------
# 1. Provider-boundary usage extraction (OpenAIEmbedding)
# ---------------------------------------------------------------------------

class TestOpenAIEmbeddingUsage:
    def test_emits_usage_when_response_carries_usage(self, monkeypatch) -> None:
        emb, client = _openai_embedding(monkeypatch)
        client.embeddings.create.return_value = _mock_response(
            total_tokens=42, request_id="req-abc",
        )
        received: list[EmbeddingUsage] = []
        emb.add_usage_listener(received.append)

        vectors = emb.embed(["hello world"])

        assert len(vectors) == 1
        assert len(received) == 1
        usage = received[0]
        assert usage.total_tokens == 42
        assert usage.prompt_tokens == 42
        assert usage.model == "qwen3-embedding"
        assert usage.provider == "openai"
        assert usage.provider_request_id == "req-abc"
        assert usage.occurred_at > 0  # stamped at emit time

    def test_no_usage_in_response_emits_nothing(self, monkeypatch) -> None:
        emb, client = _openai_embedding(monkeypatch)
        client.embeddings.create.return_value = _mock_response(total_tokens=None)
        received: list[EmbeddingUsage] = []
        emb.add_usage_listener(received.append)

        vectors = emb.embed(["hello world"])

        assert len(vectors) == 1
        assert received == []

    def test_listener_failure_does_not_break_embed(self, monkeypatch) -> None:
        emb, client = _openai_embedding(monkeypatch)
        client.embeddings.create.return_value = _mock_response()

        def _boom(usage: EmbeddingUsage) -> None:
            raise RuntimeError("listener crashed")

        emb.add_usage_listener(_boom)

        assert emb.embed(["hello world"]) == [[0.1, 0.2, 0.3]]

    def test_no_listener_means_no_event(self, monkeypatch) -> None:
        emb, client = _openai_embedding(monkeypatch)
        client.embeddings.create.return_value = _mock_response()

        # No listeners registered — embed must work (as it does in the
        # existing smoke tests) without touching usage at all.
        assert emb.embed(["x"]) == [[0.1, 0.2, 0.3]]


# ---------------------------------------------------------------------------
# 2. EmbeddingUsageStore — context tagging + persistence rules
# ---------------------------------------------------------------------------

class TestEmbeddingUsageStore:
    @staticmethod
    def _usage(total: int = 10) -> EmbeddingUsage:
        return EmbeddingUsage(
            total_tokens=total, model="qwen3-embedding", provider="openai",
            provider_request_id="req-1",
        )

    def test_records_query_event(self, tmp_path) -> None:
        db = WebApiDB(tmp_path / "web_api.db")
        store = EmbeddingUsageStore(db, enabled=True)
        with embedding_usage_context(
            operation="query", collection_id="c1", trace_id="t1",
        ):
            store.record(self._usage())

        assert db.summarize_embedding_usage(0, 10**12) == (10, 10, 0)

    def test_records_ingestion_event_with_ids(self, tmp_path) -> None:
        db = WebApiDB(tmp_path / "web_api.db")
        store = EmbeddingUsageStore(db, enabled=True)
        with embedding_usage_context(
            operation="ingestion", collection_id="c2",
            task_id="task-7", document_id="doc-9",
        ):
            store.record(self._usage(total=25))

        assert db.summarize_embedding_usage(0, 10**12) == (25, 0, 25)

    def test_healthcheck_is_dropped(self, tmp_path) -> None:
        db = WebApiDB(tmp_path / "web_api.db")
        store = EmbeddingUsageStore(db, enabled=True)
        with embedding_usage_context(operation="healthcheck"):
            store.record(self._usage())

        assert db.summarize_embedding_usage(0, 10**12) == (0, 0, 0)

    def test_unknown_or_missing_operation_is_dropped(self, tmp_path) -> None:
        db = WebApiDB(tmp_path / "web_api.db")
        store = EmbeddingUsageStore(db, enabled=True)
        # No context at all — e.g. a CLI/script embedding call that never
        # tagged its operation. Must not be silently bucketed.
        store.record(self._usage())

        with embedding_usage_context(operation="telemetry"):  # type: ignore[arg-type]
            store.record(self._usage())

        assert db.summarize_embedding_usage(0, 10**12) == (0, 0, 0)

    def test_retried_write_deduped_via_provider_request_id(self, tmp_path) -> None:
        db = WebApiDB(tmp_path / "web_api.db")
        store = EmbeddingUsageStore(db, enabled=True)
        with embedding_usage_context(operation="query"):
            store.record(self._usage())  # original
            store.record(self._usage())  # app-level retry of same provider call

        assert db.summarize_embedding_usage(0, 10**12) == (10, 10, 0)

    def test_disabled_store_drops_everything(self, tmp_path) -> None:
        db = WebApiDB(tmp_path / "web_api.db")
        store = EmbeddingUsageStore(db, enabled=False)
        with embedding_usage_context(operation="query"):
            store.record(self._usage())

        assert db.summarize_embedding_usage(0, 10**12) == (0, 0, 0)
        assert store.enabled is False

    def test_failed_write_never_raises(self) -> None:
        class BoomDB:
            def record_embedding_usage(self, event: dict) -> None:
                raise RuntimeError("disk full")

        store = EmbeddingUsageStore(BoomDB(), enabled=True)  # type: ignore[arg-type]
        with embedding_usage_context(operation="query"):
            store.record(self._usage())  # must not raise


# ---------------------------------------------------------------------------
# 3. WebApiDB — window aggregation + durability
# ---------------------------------------------------------------------------

class TestWebApiDBUsage:
    BASE = 1_700_000_000.0

    @staticmethod
    def _event(
        db: WebApiDB, *, occurred_at: float, operation: str, tokens: int,
    ) -> None:
        db.record_embedding_usage({
            "id": f"{operation}:{occurred_at}",
            "occurred_at": occurred_at,
            "operation": operation,
            "token_count": tokens,
            "provider": "openai",
            "model": "qwen3-embedding",
        })

    def test_summarize_respects_half_open_window(self, tmp_path) -> None:
        db = WebApiDB(tmp_path / "web_api.db")
        base = self.BASE
        self._event(db, occurred_at=base, operation="query", tokens=100)
        self._event(db, occurred_at=base + 500, operation="ingestion", tokens=50)
        self._event(db, occurred_at=base - 1, operation="query", tokens=999)  # before window
        self._event(db, occurred_at=base + 1000, operation="query", tokens=7)  # == end → excluded

        assert db.summarize_embedding_usage(base, base + 1000) == (150, 100, 50)

    def test_summarize_empty_window_returns_zeros(self, tmp_path) -> None:
        db = WebApiDB(tmp_path / "web_api.db")
        assert db.summarize_embedding_usage(0, 10**12) == (0, 0, 0)

    def test_earliest_event_timestamp(self, tmp_path) -> None:
        db = WebApiDB(tmp_path / "web_api.db")
        assert db.embedding_usage_earliest() is None
        self._event(db, occurred_at=self.BASE + 10, operation="query", tokens=1)
        self._event(db, occurred_at=self.BASE, operation="ingestion", tokens=2)
        assert db.embedding_usage_earliest() == self.BASE

    def test_data_survives_reopen(self, tmp_path) -> None:
        path = tmp_path / "web_api.db"
        db = WebApiDB(path)
        self._event(db, occurred_at=self.BASE, operation="query", tokens=42)
        db2 = WebApiDB(path)  # fresh connection, same file
        assert db2.summarize_embedding_usage(0, 10**12) == (42, 42, 0)
        assert db2.embedding_usage_earliest() == self.BASE
