"""SKDY DocReader gRPC service entrypoint.

Phase 0 skeleton. The real server (gRPC ReadStream / ListEngines, health
checks, streaming image responses, request_id tracing, graceful shutdown)
lands in Phase 1. We deliberately do NOT wire a running server here so this
skeleton never pretends to serve.
"""

from __future__ import annotations


def serve(argv: list[str] | None = None) -> int:
    """Start the DocReader gRPC server.

    Not implemented yet: Phase 1 introduces the real entrypoint
    (adapted from upstream ``docreader/main.py``).
    """
    raise NotImplementedError(
        "docreader.main.serve() is a Phase-0 placeholder; "
        "the gRPC server is implemented in Phase 1.",
    )


if __name__ == "__main__":
    raise SystemExit(serve())