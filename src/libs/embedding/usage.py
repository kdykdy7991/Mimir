"""
Embedding token-usage plumbing — PRD ``docs/prd-embedding-token-metrics.md`` §5.

Two pieces live here:

1. ``EmbeddingUsage`` — the raw usage payload a provider emits once per
   successful call, captured *at the provider-response boundary* where
   ``usage.total_tokens`` is still accessible (never re-estimated from
   text length upstream).

2. ``embedding_usage_context()`` — a :class:`ContextVar`-backed way for
   the *caller* (QueryService / IngestionService / SystemService) to tag
   the operation that is about to trigger an embedding call. The listener
   attached at composition time reads this context and turns the raw
   usage into a business ``operation``/``collection``-tagged event.

Design note (PRD §5.1): the public ``BaseEmbedding.embed()`` return type
stays ``list[list[float]]`` — changing it to a result object would ripple
through every entry point (CLI / MCP / Streamlit / Web) and the ~dozens
of test fakes that implement ``embed``. The observer pattern captures the
exact usage at the provider boundary without that blast radius.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Literal

# Business operations that may trigger an embedding call. Anything outside
# this set must not be silently bucketed — the store drops unknown ops.
UsageOperation = Literal["query", "ingestion", "healthcheck"]


@dataclass(frozen=True)
class EmbeddingUsage:
    """Raw token usage for one successful embedding provider call.

    ``occurred_at`` is the wall-clock time the provider returned usage
    (UTC epoch seconds) — the PRD's "统计时间以 Provider 调用成功并获得
    usage 的时间为准" rule. Populated by the provider at emit time.
    """

    total_tokens: int
    model: str
    provider: str
    prompt_tokens: int | None = None
    provider_request_id: str | None = None
    occurred_at: float = 0.0

    def __post_init__(self) -> None:
        if self.occurred_at <= 0.0:
            object.__setattr__(self, "occurred_at", time.time())


@dataclass(frozen=True)
class _UsageContext:
    """Business context set by the caller around an embedding call."""

    operation: UsageOperation
    collection_id: str | None = None
    trace_id: str | None = None
    task_id: str | None = None
    document_id: str | None = None


_usage_context: ContextVar[_UsageContext | None] = ContextVar(
    "embedding_usage_context", default=None,
)


def current_usage_context() -> _UsageContext | None:
    """Return the operation context active in this execution context."""
    return _usage_context.get()


@contextmanager
def embedding_usage_context(
    *,
    operation: UsageOperation,
    collection_id: str | None = None,
    trace_id: str | None = None,
    task_id: str | None = None,
    document_id: str | None = None,
):
    """Tag the surrounding code as one embedding operation.

    The provider's ``_emit_usage`` runs synchronously inside the with-
    block, so the ContextVar is visible when the usage listener merges
    the raw usage with this context. ContextVars are per-thread; the
    query / ingestion workers set the context in the same thread that
    drives the embedding call, so async and ingestion paths both work.
    """
    token = _usage_context.set(_UsageContext(
        operation=operation,
        collection_id=collection_id,
        trace_id=trace_id,
        task_id=task_id,
        document_id=document_id,
    ))
    try:
        yield
    finally:
        _usage_context.reset(token)


# A listener receives one ``EmbeddingUsage`` per successful provider call.
UsageListener = Callable[[EmbeddingUsage], None]


__all__ = [
    "EmbeddingUsage",
    "UsageListener",
    "UsageOperation",
    "current_usage_context",
    "embedding_usage_context",
]
