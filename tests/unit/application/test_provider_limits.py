from __future__ import annotations

import pytest

from src.application.services.provider_limits import LimitedEmbedding, LimitedLLM
from src.application.services.resource_limits import (
    CapacityExceeded, WorkloadBudget, WorkloadLimiter,
)


class Embedding:
    dimensions = 2
    usage_supported = False
    def embed(self, texts, **kwargs):
        return [[1.0, 2.0] for _ in texts]


class LLM:
    def __init__(self): self.calls = 0
    def chat(self, messages, **kwargs): self.calls += 1; return "ok"


def test_embedding_adapter_preserves_interface_and_exact_token_budget() -> None:
    limiter = WorkloadLimiter({
        "embedding": WorkloadBudget(concurrency=1, tokens_per_minute=3),
    })
    wrapped = LimitedEmbedding(Embedding(), limiter)
    assert wrapped.dimensions == 2
    assert wrapped.embed(["a"], _exact_token_cost=3) == [[1.0, 2.0]]
    with pytest.raises(Exception):
        wrapped.embed(["b"], _exact_token_cost=1)


def test_llm_routes_multimodal_calls_to_vlm_budget() -> None:
    limiter = WorkloadLimiter({
        "rerank": WorkloadBudget(concurrency=1),
        "vlm": WorkloadBudget(concurrency=1),
    })
    delegate = LLM()
    wrapped = LimitedLLM(delegate, limiter)
    with limiter.acquire("vlm"):
        with pytest.raises(CapacityExceeded):
            wrapped.chat([{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            ]}])
    assert wrapped.chat([{"role": "user", "content": "text"}]) == "ok"
