"""B4.3 — runtime rate limiter unit tests."""

from __future__ import annotations

import pytest

from src.web_api.mcp_connection import RuntimeRateLimiter


class _Clock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_allows_up_to_max_in_window():
    clock = _Clock()
    limiter = RuntimeRateLimiter(max_requests=3, window_seconds=60.0, clock=clock)
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False  # capped
    assert limiter.allow() is False


def test_window_expires_releases_slot():
    clock = _Clock()
    limiter = RuntimeRateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False
    clock.advance(61.0)
    assert limiter.allow() is True


def test_no_secret_leaks_through_limiter():
    """The limiter only counts events, never stores request content."""
    clock = _Clock()
    limiter = RuntimeRateLimiter(max_requests=10, window_seconds=60.0, clock=clock)
    limiter.allow()
    blob = repr(limiter.__dict__)
    assert "api_key" not in blob and "skdy_mcp_" not in blob
    assert "window_seconds" in blob


def test_invalid_args():
    with pytest.raises(ValueError):
        RuntimeRateLimiter(max_requests=0, window_seconds=60.0)
    with pytest.raises(ValueError):
        RuntimeRateLimiter(max_requests=1, window_seconds=-1.0)