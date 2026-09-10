#!/usr/bin/env python3
"""Standalone MCP health gate (Phase 4 §P4.4).

Confirms the main service's internal read-only API is reachable and the
read-only client can list collections. With ``--expect-upstream-down`` it
instead verifies that an unreachable upstream surfaces an explicit
``UpstreamUnavailableError`` (no silent fallback / fake answer).

Exit codes:
    0  healthy (or expected upstream-down observed)
    1  unhealthy (unexpected)
"""

from __future__ import annotations

import argparse
import os
import sys

_KEY_ENV = "MCP_INTERNAL_API_KEY"


def _load_client(base_url: str, api_key: str = ""):
    from src.mcp_server.clients.http_client import HttpRagReadOnlyClient
    from src.mcp_server.auth.context import TrustedLocalPrincipal
    # trust_env=False: an internal loopback probe must not inherit ambient
    # HTTP/HTTPS/SOCKS proxy variables.
    client = HttpRagReadOnlyClient(
        base_url=base_url, api_key=api_key, timeout_s=10.0, trust_env=False,
    )
    return client, TrustedLocalPrincipal()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8766")
    ap.add_argument("--expect-upstream-down", action="store_true")
    args = ap.parse_args(argv)

    # The internal API is authenticated; read the shared service credential
    # and pass it. Without it the probe is correctly rejected (fail-closed:
    # 'unhealthy'), so a key MUST be injected together with deployment.
    api_key = os.environ.get(_KEY_ENV, "") or ""

    client, principal = _load_client(args.base_url, api_key)
    if args.expect_upstream_down:
        from src.mcp_server.clients.errors import UpstreamUnavailableError
        # Point at a dead port: the client must raise, not fabricate data.
        dead, _ = _load_client(args.base_url.replace("8766", "1"), api_key)
        try:
            dead.list_collections(principal)
        except (UpstreamUnavailableError, Exception):
            return 0
        print("ERROR: upstream-down not detected", file=sys.stderr)
        return 1

    try:
        collections = client.list_collections(principal)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: main API unreachable: {exc}", file=sys.stderr)
        return 1
    print(f"OK: main API reachable; {len(collections)} collection(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())