"""
ConfigService — read-only facade for the dashboard's Overview
page (G1).

Wraps :class:`src.core.settings.Settings` and exposes a list
of :class:`ComponentCard`s the page can render in a grid.

Why a service at all?
----------------------
The page module shouldn't import ``Settings`` directly — that
would couple it to the on-disk YAML and the
``load_settings`` helper. ``ConfigService`` keeps that
dependency inside one module so the rest of the dashboard
just sees dataclasses.

Status / health
--------------
We expose ``available: bool`` and ``summary: str`` per card
so the page can render a coloured status chip even when the
underlying service hasn't been exercised (e.g. for the
"embedding" card we have no real "is it up?" check — the
configuration itself IS the signal).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.settings import Settings, load_settings


@dataclass
class ComponentCard:
    """One card on the Overview page."""
    name: str
    provider: str
    model: str
    extra: dict[str, Any] = field(default_factory=dict)
    available: bool = True
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "extra": dict(self.extra),
            "available": self.available,
            "summary": self.summary,
        }


class ConfigService:
    """
    Build a list of :class:`ComponentCard` from a
    :class:`Settings` object.

    Usage::

        svc = ConfigService.from_settings_path("./config/settings.yaml")
        for card in svc.cards():
            render(card)

    The cards are produced on every call — cheap enough that we
    don't need to cache, and avoids stale-display bugs if the
    user reloads the page.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @classmethod
    def from_settings_path(
        cls, path: str | Path = "./config/settings.yaml",
    ) -> "ConfigService":
        """Load settings from YAML, raising on parse error.

        Falls back to :class:`Settings()` defaults if the file
        doesn't exist (useful for first-run / dashboard-only
        installs).
        """
        p = Path(path)
        if p.is_file():
            return cls(load_settings(str(p)))
        return cls(Settings())

    @property
    def settings(self) -> Settings:
        return self._settings

    def cards(self) -> list[ComponentCard]:
        """Return one card per component, in display order."""
        s = self._settings
        return [
            ComponentCard(
                name="LLM",
                provider=s.llm.provider,
                model=s.llm.model,
                extra={
                    "temperature": s.llm.temperature,
                    "max_tokens": s.llm.max_tokens,
                    "base_url": s.llm.base_url or "",
                },
                summary=f"{s.llm.provider} / {s.llm.model}",
            ),
            ComponentCard(
                name="Embedding",
                provider=s.embedding.provider,
                model=s.embedding.model,
                extra={
                    "dimensions": s.embedding.dimensions,
                    "device": s.embedding.device,
                },
                summary=f"{s.embedding.provider} / {s.embedding.model} "
                         f"({s.embedding.dimensions}d)",
            ),
            ComponentCard(
                name="Vector Store",
                provider=s.vector_store.backend,
                model=s.vector_store.collection_name,
                extra={
                    "persist_path": s.vector_store.persist_path,
                },
                summary=f"chroma @ {s.vector_store.persist_path}",
            ),
            ComponentCard(
                name="Retrieval",
                provider=s.retrieval.sparse_backend,
                model=s.retrieval.fusion_algorithm,
                extra={
                    "top_k_dense": s.retrieval.top_k_dense,
                    "top_k_sparse": s.retrieval.top_k_sparse,
                    "top_k_final": s.retrieval.top_k_final,
                    "rrf_k": s.retrieval.rrf_k,
                },
                summary=(
                    f"fusion={s.retrieval.fusion_algorithm}, "
                    f"top_k_final={s.retrieval.top_k_final}"
                ),
            ),
            ComponentCard(
                name="Rerank",
                provider=s.rerank.backend,
                model=s.rerank.model,
                extra={"top_m": s.rerank.top_m},
                available=s.rerank.backend != "none",
                summary=(
                    "reranker disabled"
                    if s.rerank.backend == "none"
                    else f"{s.rerank.backend} / {s.rerank.model}"
                ),
            ),
            ComponentCard(
                name="Splitter",
                provider=s.splitter.type,
                model="",
                extra={
                    "chunk_size": s.splitter.chunk_size,
                    "chunk_overlap": s.splitter.chunk_overlap,
                },
                summary=(
                    f"{s.splitter.type} "
                    f"(size={s.splitter.chunk_size}, "
                    f"overlap={s.splitter.chunk_overlap})"
                ),
            ),
        ]


__all__ = ["ComponentCard", "ConfigService"]
