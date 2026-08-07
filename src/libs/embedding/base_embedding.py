"""
Embedding abstract base class.

Defines the unified interface for all Embedding providers (OpenAI, Azure, Ollama).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from src.libs.embedding.usage import EmbeddingUsage, UsageListener

_logger = logging.getLogger(__name__)


class BaseEmbedding(ABC):
    """
    Abstract base class for Embedding providers.

    All Embedding implementations must inherit from this class
    and implement the `embed` method.

    Token-usage observers (PRD ``docs/prd-embedding-token-metrics.md``
    §5.1): a provider that can read usage from its response should call
    :meth:`_emit_usage` after a successful call. Providers that cannot
    return usage simply never emit; ``usage_supported`` stays ``False``
    and the overview reports ``null`` rather than a fabricated number.
    """

    #: Stable provider id recorded on usage events (overridden per impl).
    provider_name: str = "unknown"
    #: Whether this provider exposes exact token usage. Only OpenAI-
    #: compatible responses carry ``usage`` today, so the other
    #: providers keep this ``False`` (→ overview token fields are null).
    usage_supported: bool = False

    def __init__(self) -> None:
        self._usage_listeners: list[UsageListener] = []

    def _listeners(self) -> list[UsageListener]:
        # Subclasses define their own __init__ and are not required to
        # call super().__init__(); initialise lazily so a listener
        # registered on any well-formed provider just works.
        listeners = getattr(self, "_usage_listeners", None)
        if listeners is None:
            listeners = self._usage_listeners = []
        return listeners

    def add_usage_listener(self, listener: UsageListener) -> None:
        """Register a callback receiving one ``EmbeddingUsage`` per
        successful provider call that reported usage. Best-effort:
        a listener exception is logged and never raised into the
        embedding caller."""
        if listener not in self._listeners():
            self._listeners().append(listener)

    def _emit_usage(self, **raw: Any) -> None:
        """Notify listeners of a successful call's exact token usage.

        Called by provider implementations immediately after a response
        that carried ``usage`` — while the response is still in scope,
        so we never re-estimate tokens from text length upstream.

        ``raw`` is merged with the provider's own model/provider ids;
        a ``provider_request_id`` (when present) is forwarded so the
        persistence layer can dedupe retries. Listener failures are
        swallowed (usage accounting must never break retrieval).
        """
        if not self._listeners():
            return
        try:
            usage = EmbeddingUsage(
                total_tokens=int(raw["total_tokens"]),
                model=str(raw.get("model") or getattr(self, "model", "")),
                provider=str(raw.get("provider") or self.provider_name),
                prompt_tokens=(
                    int(raw["prompt_tokens"])
                    if raw.get("prompt_tokens") is not None else None
                ),
                provider_request_id=raw.get("provider_request_id"),
            )
        except Exception:  # noqa: BLE001 — malformed provider payload must not fail embed()
            _logger.warning(
                "could not build embedding usage from provider response: %r",
                raw, exc_info=True,
            )
            return
        for listener in list(self._listeners()):
            try:
                listener(usage)
            except Exception:  # noqa: BLE001 — accounting is best-effort
                _logger.warning(
                    "embedding usage listener failed: %s", listener,
                    exc_info=True,
                )

    @abstractmethod
    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed.
            **kwargs: Additional provider-specific parameters.

        Returns:
            list[list[float]]: List of embedding vectors, one per input text.
                               Each vector is a list of floats.

        Raises:
            EmbeddingError: If the request fails.
        """
        pass

    def embed_single(self, text: str, **kwargs: Any) -> list[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text string to embed.
            **kwargs: Additional parameters.

        Returns:
            list[float]: Embedding vector.
        """
        results = self.embed([text], **kwargs)
        return results[0]

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """
        Return the dimensionality of the embedding vectors.

        Returns:
            int: Number of dimensions (e.g., 1536 for text-embedding-3-small).
        """
        pass


class EmbeddingError(Exception):
    """Base exception for Embedding-related errors."""
    pass


class EmbeddingConnectionError(EmbeddingError):
    """Raised when connection to Embedding provider fails."""
    pass


class EmbeddingRateLimitError(EmbeddingError):
    """Raised when rate limit is exceeded."""
    pass
