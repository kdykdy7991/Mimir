"""
``EmbeddingUsageStore`` — bridges provider usage events into ``WebApiDB``.

Wired once in the Web API composition root: the shared embedding instance
emits one :class:`EmbeddingUsage` per successful call (PRD
``docs/prd-embedding-token-metrics.md`` §5.1), and this store turns it
into a durable ``embedding_usage_events`` row.

Business rules (PRD §3.1 / §5.3):

- ``healthcheck`` calls are dropped — health probes must never move the
  overview token numbers.
- An embedding call with **no** operation context is dropped — unknown
  sources must not be silently bucketed into query or ingestion.
- Only ``query`` and ``ingestion`` are persisted, each carrying the
  caller's collection / trace / task / document ids.
- A failed write never breaks the query or ingestion that produced the
  usage; the failure is logged (best-effort accounting).
- Retried provider calls are deduped via the provider request id
  (``INSERT OR IGNORE`` on the event id).

Capability flag: ``enabled`` mirrors whether the configured provider can
return usage at all (e.g. ``OpenAIEmbedding`` → ``True``, local
sentence-transformers → ``False``). The overview reads it to decide
between ``null`` (not measurable) and ``0`` (measured, no usage).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.libs.embedding.usage import (
    EmbeddingUsage,
    current_usage_context,
)

if TYPE_CHECKING:
    from src.application.services.web_store import WebApiDB

logger = logging.getLogger(__name__)

# Only these operations count as business usage. Anything else
# (healthcheck, unknown/no context) is dropped at the boundary.
_ACCOUNTED_OPERATIONS = frozenset({"query", "ingestion"})


class EmbeddingUsageStore:
    """Best-effort, durable collector of embedding token usage events."""

    def __init__(self, db: "WebApiDB", *, enabled: bool = True) -> None:
        self._db = db
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """Whether the configured provider can report exact usage."""
        return self._enabled

    def record(self, usage: EmbeddingUsage) -> None:
        """Persist one usage event, tagged with the active call context.

        Called synchronously from the provider (inside the caller's
        ``embedding_usage_context`` block, same thread). Drops
        healthchecks / unknown operations and never raises.
        """
        if not self._enabled:
            return
        ctx = current_usage_context()
        operation = ctx.operation if ctx is not None else None
        if operation not in _ACCOUNTED_OPERATIONS:
            return
        # Dedup key: provider request ids are globally unique per call,
        # so a retried write of the same provider response cannot double
        # count. Fall back to a fresh uuid when absent.
        request_id = usage.provider_request_id
        event_id = f"{usage.provider}:{request_id}" if request_id else str(uuid4())
        event: dict[str, Any] = {
            "id": event_id,
            "occurred_at": usage.occurred_at,
            "operation": operation,
            "token_count": usage.total_tokens,
            "provider": usage.provider,
            "model": usage.model,
            "collection_id": ctx.collection_id,
            "trace_id": ctx.trace_id,
            "task_id": ctx.task_id,
            "document_id": ctx.document_id,
            "provider_request_id": request_id,
        }
        try:
            self._db.record_embedding_usage(event)
        except Exception:  # noqa: BLE001 — accounting must not break the caller
            logger.warning(
                "failed to persist embedding usage event "
                "(operation=%s, tokens=%s): %s",
                operation, usage.total_tokens,
                type(self._db).__name__,
                exc_info=True,
            )


__all__ = ["EmbeddingUsageStore"]
