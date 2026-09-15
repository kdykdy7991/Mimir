"""
Architecture boundary guard for ADR 0001 (MCP-server-only RAG boundary).

ADR: ``docs/adr/0001-mcp-server-only-rag-boundary.md``.

These tests protect *new* boundaries declared by the ADR with **precise
paths and explicit whitelists**. They deliberately do NOT grep the whole
repository for ordinary words like ``agent`` / ``memory`` /
``conversation`` — third-party packages, documentation (``docs/``,
``.github/``), tests and historical naming must not produce false
alarms. The checks below are exact structural rules over ``src/``:

1. ``src/`` top-level packages are an exact allowlist: introducing an
   ``agent`` / ``planner`` / ``conversation`` / ``memory`` / ... runtime
   requires amending this test (an explicit ADR review point).
2. No file or directory at depth 1-2 below ``src/`` may carry a name
   exactly matching the forbidden Agent-runtime vocabulary.
3. The MCP *Client* SDK (``mcp.client``) may be imported from exactly
   one whitelisted place — the admin-side one-shot connection self-test
   ``src/web_api/mcp_connection.py`` (ADR §3.1 exemption). The MCP
   *server* side (``src/mcp_server/``) must never import it.

Read-only-tool / no-answer / no-LLM-import invariants already live in
``test_mcp_readonly_invariants.py`` and are intentionally not duplicated
here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

# Exact top-level layout of production code as of ADR 0001. Adding a new
# top-level package means a deliberate change to this set (review hook).
ALLOWED_TOP_LEVEL = frozenset({
    "__init__.py",
    "__pycache__",
    "application",
    "core",
    "document_parser",
    "ingestion",
    "libs",
    # "mcp_server" is the MCP *server* surface (tools, auth, transports).
    "mcp_server",
    "observability",
    "web_api",
})

# Exact basenames (without .py) that would constitute an Agent runtime.
# Matching is exact, not substring: ordinary prose/identifiers never trip it.
FORBIDDEN_BASENAMES = frozenset({
    "agent",
    "agents",
    "planner",
    "react",
    "reflection",
    "conversation",
    "conversations",
    "memory",
    "memories",
    "skills",
    "sandbox",
})

# Files allowed to import the MCP *client* SDK. Today: only the admin
# connection self-test (fixed, server-configured URL; initialize ->
# tools/list -> one list_collections; hard timeout; no loop/orchestration).
ALLOWED_MCP_CLIENT_IMPORTERS = frozenset({
    Path("web_api") / "mcp_connection.py",
})


def test_src_top_level_layout_is_exactly_allowlisted():
    actual = {p.name for p in SRC.iterdir()}
    assert actual == set(ALLOWED_TOP_LEVEL), (
        "src/ top-level layout changed; any new top-level package must be "
        "reviewed against ADR 0001 and added to ALLOWED_TOP_LEVEL explicitly."
    )


@pytest.mark.parametrize("entry", sorted(FORBIDDEN_BASENAMES))
def test_no_agent_runtime_modules_at_depth_one_or_two(entry):
    """No src/<entry>.py, src/<pkg>/<entry>.py or same-named directory."""
    offenders = []
    for candidate in (SRC / f"{entry}.py", SRC / entry):
        if candidate.exists():
            offenders.append(candidate.relative_to(SRC))
    for pkg in (p for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"):
        for candidate in (pkg / f"{entry}.py", pkg / entry):
            if candidate.exists():
                offenders.append(candidate.relative_to(SRC))
    assert not offenders, (
        f"forbidden Agent-runtime module {entry!r} introduced under src/: "
        f"{[str(o) for o in offenders]} — see ADR 0001."
    )


def _python_files_under_src() -> list[Path]:
    return [
        p for p in SRC.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def test_mcp_client_sdk_only_importable_from_whitelisted_module():
    offenders = []
    for path in _python_files_under_src():
        rel = path.relative_to(SRC)
        if rel in ALLOWED_MCP_CLIENT_IMPORTERS:
            continue
        # Module-style import only; avoid matching comments/docstrings that
        # merely mention the SDK. Check import statements / importlib forms.
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if (
                "from mcp.client" in line
                or "import mcp.client" in line
                or "importlib.import_module(\"mcp.client" in line
                or "importlib.import_module('mcp.client" in line
            ):
                offenders.append(f"{rel}: {stripped}")
    assert not offenders, (
        "mcp.client imported outside the ADR §3.1 whitelist "
        f"({sorted(str(p) for p in ALLOWED_MCP_CLIENT_IMPORTERS)}):\n"
        + "\n".join(offenders)
    )


def test_whitelisted_mcp_client_importer_still_exists():
    """Pin the exemption to a real file so a rename cannot silently widen it."""
    for rel in ALLOWED_MCP_CLIENT_IMPORTERS:
        assert (SRC / rel).is_file(), f"whitelisted file vanished: {rel}"


def test_mcp_server_side_never_uses_client_sdk():
    """Defence in depth: src/mcp_server speaks the server side only."""
    server_dir = SRC / "mcp_server"
    for path in (
        p for p in server_dir.rglob("*.py") if "__pycache__" not in p.parts
    ):
        assert "mcp.client" not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(SRC)} imports the MCP client SDK; "
            "the server side must only use mcp.server / mcp.types."
        )
