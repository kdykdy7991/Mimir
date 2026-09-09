#!/usr/bin/env python3
"""Probe a running DocReader service (liveness/readiness via ListEngines).

Usage:
    python scripts/docreader_probe.py [endpoint]
    # endpoint defaults to 127.0.0.1:50051 (see config/settings.yaml)

Exits 0 when the service responds with at least one engine; otherwise exits 1.
No document body, no bytes, no secrets are printed — only engine names.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "docreader"))

import grpc  # noqa: E402

from docreader.proto import docreader_pb2, docreader_pb2_grpc  # noqa: E402

DEFAULT_ENDPOINT = "127.0.0.1:50051"


def main() -> int:
    endpoint = os.environ.get("DOCREADER_ENDPOINT") or DEFAULT_ENDPOINT
    if len(sys.argv) > 1:
        endpoint = sys.argv[1]
    try:
        with grpc.insecure_channel(endpoint) as channel:
            stub = docreader_pb2_grpc.DocReaderStub(channel)
            resp = stub.ListEngines(docreader_pb2.ListEnginesRequest(), timeout=5)
    except Exception as exc:  # noqa: BLE001
        print(f"[docreader_probe] FAIL {endpoint}: {exc}", file=sys.stderr)
        return 1
    engines = [e.name for e in resp.engines]
    print(f"[docreader_probe] OK {endpoint} engines={engines}")
    return 0


if __name__ == "__main__":
    sys.exit(main())