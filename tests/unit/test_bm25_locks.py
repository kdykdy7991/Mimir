"""
Unit tests for the per-collection BM25 write lock
(``src.ingestion.storage.bm25_locks``).

Verifies the lock primitive itself:
- concurrent writers on the SAME collection serialize;
- different collections use independent locks (parallel);
- the lock is reentrant (the ingestion worker holds it while the
  pipeline re-acquires it for the index write).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from src.ingestion.storage.bm25_locks import bm25_write_lock

_SLEEP = 0.05


def test_same_collection_serializes() -> None:
    seen: list[int] = []

    def work() -> None:
        with bm25_write_lock("reports"):
            seen.append(threading.get_ident())
            time.sleep(_SLEEP)

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(lambda _: work(), range(4)))
    elapsed = time.monotonic() - start

    # 4 serialized critical sections (~4 × _SLEEP), never parallel.
    assert len(seen) == 4
    assert elapsed >= 3.5 * _SLEEP, f"not serialized: {elapsed:.3f}s"


def test_different_collections_run_in_parallel() -> None:
    def work(name: str) -> None:
        with bm25_write_lock(name):
            time.sleep(_SLEEP)

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(work, ["reports", "finance"]))
    elapsed = time.monotonic() - start

    # Two distinct locks → the critical sections overlap (~1 × _SLEEP).
    assert elapsed < 1.8 * _SLEEP, f"blocked across collections: {elapsed:.3f}s"


def test_lock_is_reentrant() -> None:
    with bm25_write_lock("reports"):
        # Same thread re-acquiring (worker holds collection lock while
        # the pipeline re-acquires it for the index write) must not block.
        with bm25_write_lock("reports"):
            pass


__all__ = []
