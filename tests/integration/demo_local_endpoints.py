"""
Manual demo against the user's local LLM + embedding endpoints.

This is NOT a test — it does not assert pass/fail. It runs the
same live calls as ``test_local_endpoints.py`` but prints the
actual responses, so you can eyeball the model output while
debugging or tuning prompts.

When to use this
----------------
* pytest says everything is green but you want to see what the
  LLM actually said.
* You're tweaking a prompt or temperature and want to iterate
  on the reply shape.
* You're sanity-checking a fresh model deployment (does vision
  work? is the embedding dim what settings claims?).

Endpoints exercised
-------------------
* vLLM at ``localhost:8000`` — serves
  ``Qwen3.6-35B-A3B-NVFP4`` (capabilities = {text, vision}).
* qwen3-embedding at ``localhost:8003`` — 1024-dim vectors.

Usage
-----
    # Direct (no pytest needed)
    python tests/integration/demo_local_endpoints.py

    # Or as a module
    python -m tests.integration.demo_local_endpoints
"""

from __future__ import annotations

import base64
import math
import os
import socket
import sys
import textwrap
import time
from pathlib import Path
from urllib.parse import urlparse

# Same path-bootstrap pattern as test_local_endpoints.py so
# ``python tests/integration/demo_local_endpoints.py`` finds
# the ``src`` package without any extra config.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx

from src.core.settings import load_settings
from src.libs.embedding import EmbeddingFactory
from src.libs.llm.llm_factory import LLMFactory


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# 改成你想测的本地图片路径，留空就走 1x1 透明 PNG。
# 支持 PNG / JPG / JPEG / WEBP / GIF — 后缀会被读成 data:image/<ext>。
# 优先级：DEMO_IMAGE_PATH env var > 这个常量 > 1x1 fixture。
DEMO_IMAGE_PATH: str | None = None
DEMO_IMAGE_PATH = "/home/hello/workspace/SKDY-RAG-SERVER/data/images/qwen3.6_35b_a3b_score.png"


def _split_host_port(base_url: str | None, default_port: int) -> tuple[str, int]:
    if not base_url:
        return "localhost", default_port
    parsed = urlparse(base_url)
    return (parsed.hostname or "localhost"), (parsed.port or default_port)


_settings = load_settings()
LLM_HOST, LLM_PORT = _split_host_port(_settings.llm.base_url, 8000)
EMBEDDING_HOST, EMBEDDING_PORT = _split_host_port(
    _settings.embedding.base_url, 8003,
)

# 1x1 transparent PNG, same fixture used in test_local_endpoints.py.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "2mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)


# ---------------------------------------------------------------------------
# Print helpers — keep the output scannable
# ---------------------------------------------------------------------------

def _hr(title: str) -> None:
    """Section header. Fixed width so the demo reads like a log."""
    bar = "=" * 72
    print(f"\n{bar}\n  {title}\n{bar}")


def _step(label: str) -> None:
    """Sub-step indicator."""
    print(f"\n--- {label} ---")


def _server_up(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _preview(text: str, width: int = 100) -> str:
    """Wrap and indent a long reply so it lines up under the label."""
    return textwrap.indent(textwrap.fill(text, width=width), "    ")


def _show_vector(vec: list[float], head: int = 8, tail: int = 4) -> None:
    """Print the first N and last N values of a vector, plus
    min/max so you can spot a zero-vector or weird scale
    without scrolling through 1024 numbers."""
    if not vec:
        print("    <empty vector>")
        return
    head_str = ", ".join(f"{v:+.4f}" for v in vec[:head])
    tail_str = ", ".join(f"{v:+.4f}" for v in vec[-tail:])
    print(f"    dim      : {len(vec)}")
    print(f"    head[..{head}]  : [ {head_str} ]")
    print(f"    tail[-{tail}:] : [ {tail_str} ]")
    print(f"    min/max  : {min(vec):+.4f} / {max(vec):+.4f}")


def _timed(label: str, fn, *args, **kwargs):
    """Run ``fn(*args, **kwargs)`` and print elapsed seconds.
    Helps spot a model that's suddenly slow."""
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    dt = time.perf_counter() - t0
    print(f"    {label}: {dt:.2f}s")
    return result, dt


# ---------------------------------------------------------------------------
# Demos
# ---------------------------------------------------------------------------

def demo_llm() -> None:
    _hr(f"LLM  ·  vLLM at {LLM_HOST}:{LLM_PORT}")

    llm = LLMFactory.create(_settings.llm)

    _step("factory + capabilities")
    print(f"    class     : {type(llm).__name__}")
    print(f"    model     : {llm.model}")
    print(f"    caps      : {sorted(llm.capabilities)}")

    _step("chat() — text only (中文)")
    reply, _ = _timed("elapsed", llm.chat, [
        {"role": "user", "content": "用中文回答: 1+1=?"},
    ])
    print(_preview(f"reply: {reply}"))

    _step("chat() — max_tokens=10  (应该很短)")
    reply, _ = _timed("elapsed", llm.chat, [
        {"role": "user", "content": "详细介绍量子力学"},
    ], max_tokens=10)
    print(f"    len(chars): {len(reply)}")
    print(_preview(f"reply: {reply}"))

    _step("chat() — vision (1x1 PNG)")
    image_data_url = f"data:image/png;base64,{_TINY_PNG_B64}"
    print(f"    payload   : text + 1x1 PNG ({len(_TINY_PNG_B64)} b64 chars)")
    reply, _ = _timed("elapsed", llm.chat, [
        {"role": "user", "content": [
            {"type": "text", "text": "这张图主要是什么颜色?"},
            {"type": "image_url",
             "image_url": {"url": image_data_url}},
        ]},
    ], max_tokens=64)
    print(_preview(f"reply: {reply}"))

    # 可选：传一张真图。改文件顶部 DEMO_IMAGE_PATH 常量，或设 env var。
    # 优先级：env var > 顶部常量。
    extra = os.environ.get("DEMO_IMAGE_PATH") or DEMO_IMAGE_PATH
    if extra and Path(extra).is_file():
        _step(f"chat() — vision ({Path(extra).name})")
        b64 = base64.b64encode(Path(extra).read_bytes()).decode()
        suffix = Path(extra).suffix.lstrip(".") or "png"
        local_url = f"data:image/{suffix};base64,{b64}"
        print(f"    payload   : text + {Path(extra).name} "
              f"({len(b64)} b64 chars)")
        reply, _ = _timed("elapsed", llm.chat, [
            {"role": "user", "content": [
                {"type": "text", "text": "请用中文描述这张图。"},
                {"type": "image_url",
                 "image_url": {"url": local_url}},
            ]},
        ], max_tokens=256)
        print(_preview(f"reply: {reply}"))


def demo_embedding() -> None:
    _hr(f"EMBEDDING  ·  qwen3-embedding at {EMBEDDING_HOST}:{EMBEDDING_PORT}")

    emb = EmbeddingFactory.create(_settings.embedding)

    _step("factory + dimensions")
    print(f"    class     : {type(emb).__name__}")
    # OpenAIEmbedding 把 model 存在 self.settings.model, 别的 provider 可能不一样
    _model = (
        getattr(emb, "model", None)
        or getattr(getattr(emb, "settings", None), "model", None)
        or "?"
    )
    print(f"    model     : {_model}")
    print(f"    dim       : {emb.dimensions}")

    _step("embed(['hello world'])")
    vecs, _ = _timed("elapsed", emb.embed, ["hello world"])
    _show_vector(vecs[0])

    _step("embed([4 strings])  — batch")
    texts = ["alpha", "beta", "gamma", "delta"]
    vecs, _ = _timed("elapsed", emb.embed, texts)
    print(f"    count     : {len(vecs)}")
    for t, v in zip(texts, vecs):
        print(f"    {t!r:>14} -> dim={len(v)}, "
              f"first 3 = [{v[0]:+.4f}, {v[1]:+.4f}, {v[2]:+.4f}]")

    _step("embed(['人工智能的发展趋势'])  — 中文")
    vecs, _ = _timed("elapsed", emb.embed, ["人工智能的发展趋势"])
    _show_vector(vecs[0])

    _step("semantic ordering  — cat/dog vs cat/car")
    def cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb)

    cat = emb.embed(["a cat is sitting on the mat"])[0]
    dog = emb.embed(["a dog is playing in the park"])[0]
    car = emb.embed(["the engine of a car is complex"])[0]
    sim_cd = cos(cat, dog)
    sim_cc = cos(cat, car)
    print(f"    cos(cat, dog)  = {sim_cd:+.4f}")
    print(f"    cos(cat, car)  = {sim_cc:+.4f}")
    gap = sim_cd - sim_cc
    verdict = "✓ PASS" if (sim_cd > sim_cc and gap > 0.05) else "✗ FAIL"
    print(f"    gap           = {gap:+.4f}   ({verdict}, 阈值 > 0.05)")

    _step("health  — GET /v1/models  (raw httpx)")
    with httpx.Client(timeout=5.0) as client:
        r = client.get(
            f"http://{EMBEDDING_HOST}:{EMBEDDING_PORT}/v1/models"
        )
    print(f"    status    : {r.status_code}")
    ids = [m["id"] for m in r.json().get("data", [])]
    print(f"    model ids : {ids}")
    hit = "qwen3-embedding" in ids
    print(f"    contains 'qwen3-embedding' : {'yes' if hit else 'no'}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    llm_up = _server_up(LLM_HOST, LLM_PORT)
    emb_up = _server_up(EMBEDDING_HOST, EMBEDDING_PORT)

    print(f"vLLM at {LLM_HOST}:{LLM_PORT} ........... {'UP' if llm_up else 'DOWN'}")
    print(f"qwen3-embedding at {EMBEDDING_HOST}:{EMBEDDING_PORT} ... "
          f"{'UP' if emb_up else 'DOWN'}")

    if not llm_up or not emb_up:
        print(
            "\n[!] One or both endpoints are unreachable.\n"
            "    Start them (or adjust config/settings.yaml) and retry.",
            file=sys.stderr,
        )
        return 1

    demo_llm()
    demo_embedding()

    _hr("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
