"""Container-level readiness check for the DocReader gRPC service.

Used by the Docker ``HEALTHCHECK`` (and runnable manually) to verify that the
service inside the container is genuinely serving — not just that a process has
started. gRPC channels connect lazily, so a dedicated probe performs:

1. wait for the channel to enter READY,
2. a real ``ListEngines`` round-trip,
3. confirmation that the ``builtin`` engine is available and advertises the
   minimum production formats.

Exits 0 on success and non-zero on any failure, so it works as a container
health and startup-gate probe. Local-only service: no external network calls.

Run from the container as ``python -m docreader.health_check``.
"""

from __future__ import annotations

import os
import sys

import grpc

from docreader.config import CONFIG
from docreader.proto import docreader_pb2, docreader_pb2_grpc

# Formats the ``builtin`` engine must advertise for a healthy ready state.
# Mirrors src/document_parser/grpc_transport.MIN_READY_FORMATS (kept in sync for
# the container gate; DOC/XLS/PPT gated OLE2 are intentionally excluded).
MIN_READY_FORMATS: frozenset[str] = frozenset({
    "pdf", "md", "markdown", "txt", "csv",
    "docx", "xlsx", "pptx", "epub", "xmind", "html",
})


def check(endpoint: str) -> list[str]:
    """Run the readiness check; return a list of error strings (empty = OK)."""
    errors: list[str] = []
    try:
        grpc.channel_ready_future(grpc.insecure_channel(endpoint)).result(
            timeout=_ready_timeout_seconds(),
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"channel not READY: {exc}")
        return errors

    try:
        with grpc.insecure_channel(endpoint) as channel:
            stub = docreader_pb2_grpc.DocReaderStub(channel)
            resp = stub.ListEngines(
                docreader_pb2.ListEnginesRequest(), timeout=_ready_timeout_seconds(),
            )
    except grpc.RpcError as exc:
        errors.append(f"ListEngines failed: {exc}")
        return errors

    engines = {e.name: e for e in resp.engines}
    builtin = engines.get("builtin")
    if builtin is None:
        errors.append(f"no 'builtin' engine (advertised: {sorted(engines)})")
        return errors
    if not builtin.available:
        errors.append(f"'builtin' unavailable: {builtin.unavailable_reason or 'unknown'}")
    missing = MIN_READY_FORMATS - frozenset(builtin.file_types)
    if missing:
        errors.append(f"'builtin' missing required formats: {sorted(missing)}")
    return errors


def _ready_timeout_seconds() -> float:
    raw = os.environ.get("DOCREADER_HEALTH_TIMEOUT")
    if not raw:
        return 15.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 15.0


def main() -> int:
    endpoint = os.environ.get(
        "DOCREADER_HEALTH_ENDPOINT", f"127.0.0.1:{CONFIG.grpc_port}",
    )
    errors = check(endpoint)
    if errors:
        print(f"[docreader-health] FAIL {endpoint}: " + "; ".join(errors), file=sys.stderr)
        return 1
    print(f"[docreader-health] OK {endpoint}: builtin engine ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())