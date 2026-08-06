"""
Per-collection BM25 write locks (process-local).

The on-disk BM25 index (``<collection>.json``) is mutated through a
read-modify-write cycle — ``load → add/remove → atomic save`` — and the
Web API runs multiple threads (FastAPI thread pool + ingestion worker
threads). Without a lock, two threads could both ``load`` the same
index, mutate their own copy, and ``save`` — the last writer silently
drops the other's updates.

This module provides one **reentrant** lock per collection, shared by
every writer:

- the ingestion pipeline (``_merge_into_bm25``)
- ``DocumentManager.delete_document`` (the on-disk BM25 removal)

Callers that also perform the cache-invalidate step (ingestion worker /
delete routers) hold the *same* lock across the whole write sequence so
``load → add/remove → atomic save → cache invalidate`` is serialized per
collection. ``RLock`` makes re-entrant acquisition safe (e.g. a caller
holding the collection lock while the pipeline re-acquires it for the
index write).

Cross-process note
------------------
The registry is **process-local** — it does not coordinate between
multiple Uvicorn worker processes (or separate processes) touching the
same ``data/`` directory. Until a shared cross-process lock (file lock /
DB advisory lock) lands, deployments MUST run a **single worker**
(``uvicorn`` defaults to 1; see ``src/web_api/main.py`` and README
「并发与部署」).
"""

from __future__ import annotations

import threading
from typing import Dict

_locks: Dict[str, threading.RLock] = {}
_guard = threading.Lock()


def bm25_write_lock(collection: str) -> threading.RLock:
    """Return the process-wide write lock for ``collection``.

    The registry is bounded by the number of distinct collections a
    process touches (local scale), so it is fine to keep every lock
    alive for the life of the process.
    """
    with _guard:
        lock = _locks.get(collection)
        if lock is None:
            lock = threading.RLock()
            _locks[collection] = lock
        return lock


__all__ = ["bm25_write_lock"]
