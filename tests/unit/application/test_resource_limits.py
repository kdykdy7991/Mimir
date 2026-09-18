from __future__ import annotations

import pytest

from src.application.services.resource_limits import (
    CapacityExceeded, RateLimitExceeded, WorkloadBudget, WorkloadLimiter,
)


def test_concurrency_is_fail_fast_and_released() -> None:
    limiter = WorkloadLimiter({"mcp": WorkloadBudget(concurrency=1)})
    with limiter.acquire("mcp", subject="key-a"):
        with pytest.raises(CapacityExceeded):
            with limiter.acquire("mcp", subject="key-a"):
                pass
        with limiter.acquire("mcp", subject="key-b"):
            pass
    with limiter.acquire("mcp", subject="key-a"):
        pass


def test_rolling_rpm_has_retry_after() -> None:
    now = [100.0]
    limiter = WorkloadLimiter(
        {"mcp": WorkloadBudget(concurrency=2, requests_per_minute=2)},
        clock=lambda: now[0],
    )
    for _ in range(2):
        with limiter.acquire("mcp", subject="key-a"):
            pass
    with pytest.raises(RateLimitExceeded) as rejected:
        with limiter.acquire("mcp", subject="key-a"):
            pass
    assert rejected.value.retry_after_seconds == 60
    now[0] = 160
    with limiter.acquire("mcp", subject="key-a"):
        pass


def test_exact_tpm_rejects_without_recording_rejected_cost() -> None:
    limiter = WorkloadLimiter({
        "embedding": WorkloadBudget(concurrency=1, tokens_per_minute=10),
    })
    with limiter.acquire("embedding", token_cost=7):
        pass
    with pytest.raises(RateLimitExceeded):
        with limiter.acquire("embedding", token_cost=4):
            pass
    with limiter.acquire("embedding", token_cost=3):
        pass
