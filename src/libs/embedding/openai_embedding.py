"""
OpenAI Embedding implementation.

Uses the official OpenAI Python SDK. Works against any
OpenAI-compatible /v1/embeddings endpoint:
- OpenAI
- vLLM (text-embedding endpoints)
- Ollama (with OpenAI compatibility mode)
- TEI / Infinity / sentence-transformers behind an OpenAI shim

Model auto-discovery
--------------------
At construction time we call ``GET {base_url}/v1/models`` and
pick the model id actually served by the live instance:

- 1 model served              -> use it
- N>1, configured in list     -> use configured
- N>1, configured NOT in list -> use first served, log WARNING
- 0 models served             -> fall back to ``settings.model``
- API unreachable / error     -> raise ``EmbeddingConnectionError``

The configured ``settings.embedding.model`` is treated as a
hint, not a contract. The discovered id is written back onto
``self.settings.model`` so consumers (e.g. the dashboard's
``ConfigService``) see the live truth.
"""

from __future__ import annotations

import logging
from typing import Any

from src.libs.embedding.base_embedding import (
    BaseEmbedding,
    EmbeddingConnectionError,
    EmbeddingError,
)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore

_logger = logging.getLogger(__name__)

# 启动时探测 /v1/models 的超时——比 client 级超时短,因为这只是 init 探针,
# 失败要快速 fail-fast。SDK 自带 2 次重试,最坏 ~15s。
DISCOVERY_TIMEOUT: float = 5.0


class OpenAIEmbedding(BaseEmbedding):
    """
    OpenAI Embedding provider.

    Uses the official openai SDK. Auto-discovers the model id
    from the API at construction time — see the module docstring.
    """

    def __init__(self, settings: Any):
        if OpenAI is None:
            raise EmbeddingError(
                "openai package is not installed. "
                "Install it with: pip install openai"
            )
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url or "https://api.openai.com/v1",
        )
        self._dimensions = settings.dimensions

        # --- Auto-discover the model actually served by the API ---
        # The configured `settings.embedding.model` is treated as a
        # hint; the live API's /v1/models response is the source of
        # truth. See _discover_model() for the full decision tree.
        self.model = self._discover_model()

        # Keep the dashboard's ConfigService in sync: it reads
        # `s.embedding.model` to render the Embedding card, so we
        # write the discovered id back onto settings.
        self.settings.model = self.model

        _logger.info(
            "Discovered embedding model: %s "
            "(configured hint: %s, base_url: %s)",
            self.model,
            settings.model,
            settings.base_url or "https://api.openai.com/v1",
        )

    def _discover_model(self) -> str:
        """
        Call ``GET {base_url}/v1/models`` via the OpenAI SDK and
        pick a model id.

        Fail hard if the API is unreachable; fall back to
        ``settings.embedding.model`` only when the API responds
        but returns zero models.
        """
        try:
            page = self.client.models.list(timeout=DISCOVERY_TIMEOUT)
            ids = [m.id for m in page.data if getattr(m, "id", None)]
        except Exception as e:                       # network / 4xx / 5xx / parse
            msg = (
                f"Could not auto-discover embedding model from "
                f"{self.client.base_url}/v1/models: {e}"
            )
            _logger.error(msg)
            raise EmbeddingConnectionError(msg) from e

        if len(ids) == 0:
            # API reachable but empty. Trust the configured hint.
            _logger.warning(
                "Embedding API returned no models; falling back to "
                "settings.embedding.model=%r",
                self.settings.model,
            )
            return self.settings.model

        if len(ids) == 1:
            return ids[0]

        # N > 1: use the configured hint if it matches, else first.
        configured = self.settings.model
        if configured in ids:
            return configured
        _logger.warning(
            "Embedding API returned %d models and configured "
            "model %r is not among them; using first available: %r. "
            "Available: %s",
            len(ids), configured, ids[0], ids,
        )
        return ids[0]

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            texts: List of text strings to embed.
            **kwargs: Additional parameters.

        Returns:
            list[list[float]]: List of embedding vectors.

        Raises:
            EmbeddingConnectionError: If connection fails.
            EmbeddingError: For other errors.
        """
        if not texts:
            return []

        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=texts,
            )
            # Sort by index to ensure correct order
            sorted_data = sorted(response.data, key=lambda x: x.index)
            return [item.embedding for item in sorted_data]
        except Exception as e:
            error_msg = str(e).lower()
            if "connection" in error_msg or "timeout" in error_msg:
                raise EmbeddingConnectionError(
                    f"OpenAI Embedding connection failed: {e}"
                ) from e
            raise EmbeddingError(f"OpenAI Embedding error: {e}") from e

    @property
    def dimensions(self) -> int:
        """Return embedding dimensions."""
        return self._dimensions
