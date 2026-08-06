"""
Live integration tests against the user's local endpoints.

These tests hit real HTTP services, so they skip gracefully
when the servers are not reachable (the typical CI case).
Run them on a machine where both servers are up to lock in
the integration contract.

Endpoints exercised
-------------------
* vLLM at ``localhost:8000`` — serves
  ``Qwen3.6-35B-A3B-NVFP4`` (capabilities = {text, vision}).
* qwen3-embedding at ``localhost:8003`` — 1024-dim vectors.

What the tests assert
---------------------
* The project's ``LLMFactory`` / ``EmbeddingFactory`` actually
  produce instances that round-trip with the local servers.
* The capability detection (``capabilities`` property) returns
  ``{text, vision}`` for the configured LLM.
* Vision input is actually accepted by the LLM server (not
  just declared in ``VISION_MODELS``).
* Embedding output dim matches ``settings.embedding.dimensions``.
* Embedding semantic ordering: similar pairs score higher
  than dissimilar pairs.

Usage
-----
    # Run only these (skips fast if servers are down)
    pytest tests/integration/test_local_endpoints.py -v

    # Or, if you want them in the regular suite
    pytest tests/integration/ -v
"""

from __future__ import annotations

import base64
import math
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

# Allow ``python tests/integration/test_local_endpoints.py`` to
# import the ``src`` package. Pytest adds the project root to
# sys.path automatically; plain ``python`` does not.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx
import pytest

from src.core.settings import load_settings
from src.libs.embedding import EmbeddingFactory
from src.libs.llm.llm_factory import LLMFactory


# ---------------------------------------------------------------------------
# Configuration: derived from config/settings.yaml
# ---------------------------------------------------------------------------
# We deliberately do NOT hardcode ports / hosts here — the
# config file is the single source of truth. Change the
# ``base_url`` there and the test follows.

def _split_host_port(base_url: str | None, default_port: int) -> tuple[str, int]:
    """Pull (host, port) out of an OpenAI-style base URL.

    Falls back to ``("localhost", default_port)`` if the URL
    is missing or unparseable, so the test is still useful in
    a misconfigured environment — pytest will skip with a
    clear reason rather than crash.
    """
    if not base_url:
        return "localhost", default_port
    parsed = urlparse(base_url)
    return (parsed.hostname or "localhost"), (parsed.port or default_port)


# Load settings once at import time so the module-level
# skipif can use the host/port without re-parsing for every
# test. If your config is in a non-default location, point
# the project root at it before running pytest (the test
# uses ``load_settings()`` which honours the standard path).
_settings = load_settings()
LLM_HOST, LLM_PORT = _split_host_port(_settings.llm.base_url, 8000)
EMBEDDING_HOST, EMBEDDING_PORT = _split_host_port(
    _settings.embedding.base_url, 8003,
)

# 1x1 transparent PNG (matches the fixture used elsewhere in
# the project). Sent as a data: URL — no file IO needed.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "2mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _server_up(host: str, port: int, timeout: float = 2.0) -> bool:
    """Quick TCP probe — used to decide whether to skip."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


# Skip the whole module if either server is down. The skip
# reason shows up in pytest output so a user can see why.
pytestmark = pytest.mark.skipif(
    not (_server_up(LLM_HOST, LLM_PORT)
         and _server_up(EMBEDDING_HOST, EMBEDDING_PORT)),
    reason=(
        f"local endpoints unreachable "
        f"(need vLLM at {LLM_HOST}:{LLM_PORT} and "
        f"qwen3-embedding at {EMBEDDING_HOST}:{EMBEDDING_PORT})"
    ),
)


def _settings_fresh():
    """Reload settings — useful when a test mutates config in-place."""
    return load_settings()


def _llm():
    return LLMFactory.create(_settings_fresh().llm)


def _embedding():
    return EmbeddingFactory.create(_settings_fresh().embedding)


# ---------------------------------------------------------------------------
# LLM endpoint — vLLM at :8000
# ---------------------------------------------------------------------------

class TestLLMEndpoint:
    """Live vLLM at localhost:8000."""

    def test_factory_creates_openai_compatible(self):
        llm = _llm()
        assert type(llm).__name__ == "OpenAICompatibleLLM"
        assert llm.model == "Qwen3.6-35B-A3B-NVFP4"

    def test_capabilities_contain_vision(self):
        """The configured model is on VISION_MODELS, so
        capabilities must include 'vision'."""
        llm = _llm()
        assert "text" in llm.capabilities
        assert "vision" in llm.capabilities

    def test_chat_returns_non_empty_text(self):
        """Trivial text Q&A — proves the chat() path is wired."""
        llm = _llm()
        reply = llm.chat(
            [{"role": "user", "content": "用中文回答: 1+1=?"}],
        )
        assert isinstance(reply, str)
        assert reply.strip(), "reply is empty"
        assert "2" in reply, f"reply doesn't contain 2: {reply!r}"

    def test_chat_respects_max_tokens(self):
        """A short max_tokens must cap the response length.
        We pick a question that would normally elicit a long
        answer; if max_tokens=10 actually clamps, the reply
        is short."""
        llm = _llm()
        reply = llm.chat(
            [{"role": "user", "content": "详细介绍量子力学"}],
            max_tokens=10,
        )
        # 10 tokens is roughly ≤ 30 CJK characters; allow some
        # headroom for tokenization variance.
        assert len(reply) <= 80, (
            f"max_tokens=10 should cap reply; got {len(reply)} chars: {reply!r}"
        )

    def test_image_input_is_accepted(self):
        """Send a 1x1 PNG and verify the LLM actually consumes
        it. We don't assert on the description content (too
        model-specific) — just that the response is non-empty
        and the call returns within the timeout."""
        llm = _llm()
        image_data_url = f"data:image/png;base64,{_TINY_PNG_B64}"
        # Note: chat() takes a list of message dicts; for
        # vision, the content field is a list of typed blocks
        # rather than a string. The OpenAICompatibleLLM passes
        # that through to the OpenAI SDK unchanged.
        reply = llm.chat(
            [{"role": "user", "content": [
                {"type": "text", "text": "这张图主要是什么颜色?"},
                {"type": "image_url",
                 "image_url": {"url": image_data_url}},
            ]}],
            max_tokens=64,
        )
        assert isinstance(reply, str)
        assert reply.strip(), "image-input reply is empty"


# ---------------------------------------------------------------------------
# Embedding endpoint — qwen3-embedding at :8003
# ---------------------------------------------------------------------------

class TestEmbeddingEndpoint:
    """Live qwen3-embedding at localhost:8003."""

    def test_factory_creates_openai_embedding(self):
        emb = _embedding()
        assert type(emb).__name__ == "OpenAIEmbedding"

    def test_dimensions_match_settings(self):
        """The settings declare 1024; the model must return 1024."""
        emb = _embedding()
        assert emb.dimensions == 1024

    def test_embed_single_text_returns_1024d_vector(self):
        emb = _embedding()
        vecs = emb.embed(["hello world"])
        assert len(vecs) == 1
        assert len(vecs[0]) == 1024
        # A zero vector would be a sign of an unconfigured model.
        assert any(v != 0.0 for v in vecs[0]), "vector is all zeros"

    def test_embed_batch_preserves_order(self):
        emb = _embedding()
        texts = ["alpha", "beta", "gamma", "delta"]
        vecs = emb.embed(texts)
        assert len(vecs) == 4
        for v in vecs:
            assert len(v) == 1024
        # Pairwise: each pair should differ (not all identical).
        assert vecs[0] != vecs[1]
        assert vecs[0] != vecs[2]

    def test_chinese_text_produces_valid_vector(self):
        emb = _embedding()
        vecs = emb.embed(["人工智能的发展趋势"])
        assert len(vecs) == 1
        assert len(vecs[0]) == 1024
        assert any(v != 0.0 for v in vecs[0])

    def test_semantic_ordering(self):
        """Similar pairs should score higher than dissimilar
        pairs. We don't pin exact values — just the relative
        ordering, which is what a retrieval system relies on."""
        emb = _embedding()
        # English pair: cat/dog close, cat/car far
        e_cat = emb.embed(["a cat is sitting on the mat"])[0]
        e_dog = emb.embed(["a dog is playing in the park"])[0]
        e_car = emb.embed(["the engine of a car is complex"])[0]

        def cos(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            return dot / (na * nb)

        sim_cat_dog = cos(e_cat, e_dog)
        sim_cat_car = cos(e_cat, e_car)
        # The two scores should differ by a non-trivial margin.
        # We require 0.05 — generous enough to avoid flakiness
        # from a single 1x1 test, tight enough to catch a
        # broken model.
        assert sim_cat_dog > sim_cat_car, (
            f"cat/dog ({sim_cat_dog:.3f}) should be more similar "
            f"than cat/car ({sim_cat_car:.3f})"
        )
        assert sim_cat_dog - sim_cat_car > 0.05, (
            f"similarity gap too small: "
            f"cat/dog={sim_cat_dog:.3f}, cat/car={sim_cat_car:.3f}"
        )

    def test_health_endpoint_via_httpx(self):
        """Raw /v1/models reachability — useful when the
        factory-level tests above give a confusing failure."""
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"http://{EMBEDDING_HOST}:{EMBEDDING_PORT}/v1/models")
        assert r.status_code == 200
        ids = [m["id"] for m in r.json().get("data", [])]
        assert "qwen3-embedding" in ids, (
            f"qwen3-embedding not in /v1/models response: {ids}"
        )

    def test_factory_discovered_model_matches_live_models_endpoint(self):
        """The EmbeddingFactory's auto-discovery should pick a
        model id that is in the live /v1/models response. This
        is the end-to-end check that the discovery plumbing
        works against the real server, not just mocks."""
        # Live list of deployed model ids.
        with httpx.Client(timeout=5.0) as client:
            r = client.get(
                f"http://{EMBEDDING_HOST}:{EMBEDDING_PORT}/v1/models"
            )
        live_ids = [m["id"] for m in r.json().get("data", [])]
        assert live_ids, "live /v1/models returned no models"

        # Factory-built instance must agree with the live list.
        emb = _embedding()
        assert emb.model in live_ids, (
            f"factory picked {emb.model!r} but live /v1/models "
            f"offers: {live_ids}"
        )
        # Tighter regression check for the known deployment.
        assert emb.model == "qwen3-embedding"

        # The dashboard's ConfigService reads settings.embedding.model;
        # OpenAIEmbedding.__init__ mutates it to the discovered id so
        # the dashboard shows the live truth. Verify that side effect.
        assert _settings_fresh().embedding.model == emb.model


# ---------------------------------------------------------------------------
# Allow ``python tests/integration/test_local_endpoints.py`` to run the
# suite with the same output as ``pytest -v``. Convenient for ad-hoc
# checks; CI should still use ``pytest`` directly.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))
