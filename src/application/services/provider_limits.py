"""Transparent provider adapters enforcing Task 08 workload budgets."""

from __future__ import annotations

from typing import Any

from src.application.services.resource_limits import WorkloadLimiter


class LimitedEmbedding:
    def __init__(self, delegate: Any, limiter: WorkloadLimiter) -> None:
        self._delegate = delegate
        self._limiter = limiter

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        exact = int(kwargs.pop("_exact_token_cost", 0))
        with self._limiter.acquire("embedding", token_cost=exact):
            return self._delegate.embed(texts, **kwargs)

    def embed_single(self, text: str, **kwargs: Any) -> list[float]:
        return self.embed([text], **kwargs)[0]

    @property
    def dimensions(self) -> int:
        return self._delegate.dimensions

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class LimitedReranker:
    def __init__(self, delegate: Any, limiter: WorkloadLimiter) -> None:
        self._delegate = delegate
        self._limiter = limiter

    def rerank(self, query: str, candidates: list[Any], **kwargs: Any) -> list[Any]:
        exact = int(kwargs.pop("_exact_token_cost", 0))
        with self._limiter.acquire("rerank", token_cost=exact):
            return self._delegate.rerank(query, candidates, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class LimitedLLM:
    def __init__(self, delegate: Any, limiter: WorkloadLimiter) -> None:
        self._delegate = delegate
        self._limiter = limiter

    def chat(self, messages: list[dict], **kwargs: Any) -> str:
        exact = int(kwargs.pop("_exact_token_cost", 0))
        workload = "vlm" if _has_image(messages) else "rerank"
        with self._limiter.acquire(workload, token_cost=exact):
            return self._delegate.chat(messages, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def _has_image(messages: list[dict]) -> bool:
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(block, dict)
            and block.get("type") in {"image", "image_url"}
            for block in content
        ):
            return True
    return False


__all__ = ["LimitedEmbedding", "LimitedLLM", "LimitedReranker"]
