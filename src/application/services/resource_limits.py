"""In-process concurrency and rolling-window workload budgets."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Literal

Workload = Literal["docreader", "embedding", "rerank", "vlm", "mcp"]


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("rate budget exceeded")
        self.retry_after_seconds = max(0.001, retry_after_seconds)


class CapacityExceeded(RuntimeError):
    """The configured concurrent workload capacity is exhausted."""


@dataclass(frozen=True)
class WorkloadBudget:
    concurrency: int
    requests_per_minute: int | None = None
    tokens_per_minute: int | None = None

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be positive")
        if self.requests_per_minute is not None and self.requests_per_minute < 1:
            raise ValueError("requests_per_minute must be positive")
        if self.tokens_per_minute is not None and self.tokens_per_minute < 1:
            raise ValueError("tokens_per_minute must be positive")


class WorkloadLimiter:
    """Fail-fast concurrency plus exact rolling RPM/TPM accounting."""

    def __init__(
        self, budgets: dict[Workload, WorkloadBudget],
        *, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._budgets = dict(budgets)
        self._clock = clock
        self._lock = threading.Lock()
        self._active: dict[tuple[Workload, str], int] = defaultdict(int)
        self._requests: dict[tuple[Workload, str], deque[float]] = defaultdict(deque)
        self._tokens: dict[tuple[Workload, str], deque[tuple[float, int]]] = defaultdict(deque)

    @contextmanager
    def acquire(
        self, workload: Workload, *, subject: str = "global", token_cost: int = 0,
    ) -> Iterator[None]:
        if token_cost < 0:
            raise ValueError("token_cost cannot be negative")
        budget = self._budgets[workload]
        key = (workload, subject)
        now = self._clock()
        with self._lock:
            self._prune(key, now)
            if self._active[key] >= budget.concurrency:
                raise CapacityExceeded("workload concurrency exhausted")
            requests = self._requests[key]
            if budget.requests_per_minute is not None and len(requests) >= budget.requests_per_minute:
                raise RateLimitExceeded(60.0 - (now - requests[0]))
            tokens = self._tokens[key]
            used = sum(count for _, count in tokens)
            if budget.tokens_per_minute is not None and used + token_cost > budget.tokens_per_minute:
                retry = 60.0 - (now - tokens[0][0]) if tokens else 60.0
                raise RateLimitExceeded(retry)
            self._active[key] += 1
            requests.append(now)
            if token_cost:
                tokens.append((now, token_cost))
        try:
            yield
        finally:
            with self._lock:
                self._active[key] -= 1

    def _prune(self, key: tuple[Workload, str], now: float) -> None:
        cutoff = now - 60.0
        while self._requests[key] and self._requests[key][0] <= cutoff:
            self._requests[key].popleft()
        while self._tokens[key] and self._tokens[key][0][0] <= cutoff:
            self._tokens[key].popleft()


def default_workload_limiter(
    *, mcp_concurrency: int = 4, mcp_requests_per_minute: int = 600,
) -> WorkloadLimiter:
    return WorkloadLimiter({
        "docreader": WorkloadBudget(concurrency=2),
        "embedding": WorkloadBudget(concurrency=4, requests_per_minute=600),
        "rerank": WorkloadBudget(concurrency=2, requests_per_minute=300),
        "vlm": WorkloadBudget(concurrency=1, requests_per_minute=60),
        "mcp": WorkloadBudget(
            concurrency=mcp_concurrency,
            requests_per_minute=mcp_requests_per_minute,
        ),
    })


__all__ = [
    "CapacityExceeded", "RateLimitExceeded", "WorkloadBudget",
    "WorkloadLimiter", "default_workload_limiter",
]
