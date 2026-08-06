"""
Uvicorn entry for the Web API.

Run locally::

    python -m src.web_api.main
    # or, with the package on PYTHONPATH:
    uvicorn src.web_api.app:app --reload

Defaults bind to ``127.0.0.1:8766`` so the Web API does not collide
with the MCP streamable-http transport default (``8765``).

Concurrency
-----------
The server **must run a single worker process**. The BM25 write lock
(``src/ingestion/storage/bm25_locks.py``) is process-local and only
coordinates threads within one process; two workers sharing ``data/``
would race on the on-disk index. ``uvicorn`` defaults to one worker and
this entry point does not expose ``--workers`` — do not launch the app
with ``uvicorn src.web_api.app:app --workers N`` for N>1 until a shared
cross-process lock exists (see README「并发与部署」).
"""

from __future__ import annotations

import argparse
import logging
from typing import Sequence

import uvicorn

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766  # +1 over streamable-http default to avoid collisions


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="skdy-rag-web",
        description="Web API for the SKDY RAG Server (M1: stub endpoints).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--reload", action="store_true",
        help="Enable auto-reload (dev only).",
    )
    parser.add_argument(
        "--log-level", default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logger.info(
        "starting SKDY RAG Web API on http://%s:%d (docs at /docs)",
        args.host, args.port,
    )
    uvicorn.run(
        "src.web_api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        access_log=False,
        # Single worker is REQUIRED (BM25 write lock is process-local).
        # See the module docstring; do not add a --workers flag.
        workers=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
