"""
Health check helpers for the Dashboard Overview page.

Lightweight connectivity checks for OpenAI-compatible endpoints
(LLM / Embedding) without consuming tokens. We only hit
``/v1/models`` and optionally verify that the configured model is
present in the server's model list.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


DEFAULT_TIMEOUT = 5.0


@dataclass
class HealthResult:
    """Outcome of a single health probe."""

    ok: bool
    message: str
    latency_ms: float | None = None


def check_openai_compatible(
    base_url: str,
    expected_model: str | None = None,
    api_key: str = "not-needed",
    timeout: float = DEFAULT_TIMEOUT,
) -> HealthResult:
    """
    Probe an OpenAI-compatible endpoint by calling ``GET /v1/models``.

    Parameters
    ----------
    base_url:
        Root URL of the OpenAI-compatible server, e.g.
        ``http://localhost:8000/v1``.
    expected_model:
        If provided, verify the model appears in the server's model list.
    api_key:
        Bearer token for the request. Local servers usually accept any
        value; use an empty string to skip the Authorization header.
    timeout:
        Request timeout in seconds.

    Returns
    -------
    HealthResult
        ``ok=True`` when the server responds and the expected model is
        found; otherwise ``ok=False`` with a human-readable reason.
    """
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        url = base + "/models"
    else:
        url = base + "/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = time.perf_counter()

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
    except httpx.TimeoutException:
        return HealthResult(
            ok=False,
            message=f"连接超时({timeout}s)，请检查服务是否已启动",
        )
    except httpx.ConnectError:
        return HealthResult(
            ok=False,
            message=f"无法连接到 {base_url}",
        )
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:200]
        return HealthResult(
            ok=False,
            message=f"服务返回错误：HTTP {exc.response.status_code} - {detail}",
        )
    except Exception as exc:  # noqa: BLE001
        return HealthResult(ok=False, message=f"检测失败：{exc}")

    elapsed_ms = (time.perf_counter() - started) * 1000
    data = resp.json()
    models = [m.get("id", "") for m in data.get("data", [])]

    if expected_model and expected_model not in models:
        available = ", ".join(models[:5]) or "无"
        return HealthResult(
            ok=False,
            message=f"服务在线，但未找到模型 {expected_model}。可用模型：{available}",
            latency_ms=elapsed_ms,
        )

    model_msg = f"，模型 {expected_model} 已加载" if expected_model else ""
    return HealthResult(
        ok=True,
        message=f"连通{model_msg}，延迟 {elapsed_ms:.0f}ms",
        latency_ms=elapsed_ms,
    )


__all__ = ["DEFAULT_TIMEOUT", "HealthResult", "check_openai_compatible"]
