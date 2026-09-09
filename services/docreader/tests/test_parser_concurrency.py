"""Regression test for the process-wide parser-concurrency limiter.

Directly migrated from WeKnora ``docreader/tests/test_parser_concurrency.py``
@ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4. Covers disabled throttling and
correct serialization under a BoundedSemaphore.
"""

from __future__ import annotations

import threading
import time

from docreader.parser.concurrency import parser_worker_limit


def test_disabled_throttle_max_workers_zero() -> None:
    entered = 0
    for _ in range(5):
        with parser_worker_limit("unit-disabled", 0):
            entered += 1
    assert entered == 5


def test_limiter_serializes_concurrent_entries() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()
    results = []

    def worker(n: int) -> None:
        nonlocal active, peak
        with parser_worker_limit("unit-serial", 1):
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.005)
            with lock:
                active -= 1
        results.append(n)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 1  # never more than one holder at a time
    assert sorted(results) == list(range(10))