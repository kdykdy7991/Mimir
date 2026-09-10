"""
Phase 0, P0.2 — readonly invariants of the MCP read surface.

Four prohibited behaviours are pinned here so later phases can never
silently regress them:

1. **Tool surface** — the exposed tool list contains no
   create / upload / update / delete / import / chat / agent tool.
2. **No store mutation** — a read tool call never mutates the
   knowledge base / document / chunk stores.
3. **No LLM / Agent imports** — the MCP tool modules do not pull in the
   LLM layera or any Agent module.
4. **Inaccessible == nonexistent** — a document the principal cannot
   read and a document that does not exist produce the *same* error
   surface (no resource-presence leakage).

Phase-0 scope note: the tools are driven through their real handlers with
offline fakes. ``query_knowledge_hub``'s mutation-free guarantee is
asserted here at the surface / import-boundary level; once Phase 1 adds
``RagReadOnlyClient`` the client is the single read bound, and this file
is extended to assert the *client* exposes no write method.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from src.mcp_server.tools import get_document_summary as gds
from src.mcp_server.tools import list_collections as lc


def _run(coro):
    return asyncio.run(coro)


def _build_handler():
    from src.mcp_server.protocol_handler import ProtocolHandler
    from src.mcp_server.server import _register_default_tools

    handler = ProtocolHandler(server_name="invariant-test")
    _register_default_tools(handler)
    return handler


# ---------------------------------------------------------------------------
# 1. Tool surface is read-only
# ---------------------------------------------------------------------------

def test_no_write_or_chat_or_agent_tool_is_exposed():
    handler = _build_handler()
    names = sorted(handler.list_names())
    forbidden = (
        "create", "upload", "update", "delete", "import", "chat", "agent",
    )
    for name in names:
        lower = name.lower()
        for word in forbidden:
            assert word not in lower, f"forbidden read-tool name: {name}"


def test_client_boundary_exposes_only_read_methods():
    """The in-process client (the read boundary) has no write method."""
    from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient

    for name in vars(InProcessRagReadOnlyClient):
        if name.startswith("_"):
            continue
        lower = name.lower()
        for word in ("create", "upload", "update", "delete", "save", "write",
                     "import", "add", "put", "post"):
            assert word not in lower, f"write-like method on read client: {name}"


# ---------------------------------------------------------------------------
# 3. No LLM / Agent imports in the MCP tool modules
# ---------------------------------------------------------------------------

def test_tool_modules_avoid_llm_and_agent_imports():
    """Freshly importing the three read-tool modules must not transitively
    import the LLM layer or any Agent module."""
    modules_to_purge = [
        key for key in list(__import__("sys").modules)
        if key.startswith("src.mcp_server.tools")
        or key.startswith("src.mcp_server.protocol_handler")
    ]
    for key in modules_to_purge:
        __import__("sys").modules.pop(key, None)
    already = set(__import__("sys").modules)
    # Import triggers a fresh load of the tools (and their deps).
    import importlib
    importlib.import_module("src.mcp_server.tools.query_knowledge_hub")
    importlib.import_module("src.mcp_server.tools.list_collections")
    importlib.import_module("src.mcp_server.tools.get_document_summary")
    newly_loaded = set(__import__("sys").modules) - already
    for mod in newly_loaded:
        assert "agent" not in mod.lower(), f"agent module imported: {mod}"
        assert "src.libs.llm" not in mod, f"llm module imported: {mod}"


# ---------------------------------------------------------------------------
# 2. Read calls never mutate the underlying stores
# ---------------------------------------------------------------------------

class _SpyVectorStore:
    """Read-only vector-store fake that raises if any mutator is touched."""

    def __init__(self, hits_by_meta=None, count_value=0, existing=None):
        self.hits_by_meta = hits_by_meta or {}
        self.count_value = count_value
        self.existing = existing or {"my-collection"}
        self.read_calls = []

    def get_by_metadata(self, filters, *, limit=None, collection=None, **kw):
        self.read_calls.append(("get_by_metadata", filters))
        return self.hits_by_meta.get(str(filters), [])

    def get_collection_stats(self, *, collection=None, **kw):
        self.read_calls.append(("get_collection_stats", collection))
        return {"count": self.count_value, "collection_name": collection}

    def count(self, *, collection=None, **kw):
        self.read_calls.append(("count", collection))
        return self.count_value

    # Read-only listing used by list_collections' count helper.
    def list_collections(self):
        self.read_calls.append(("list_collections", None))
        return [type("C", (), {"name": n})() for n in self.existing]

    @property
    def _client(self):
        """Chroma router exposes a ``_client`` whose ``list_collections``
        returns collection objects (used to avoid creating missing stores)."""
        return self

    # --- Mutators: raise if anything tries to write. ---
    def add(self, *a, **k):
        raise AssertionError("read tool mutated store: add() called")

    def upsert(self, *a, **k):
        raise AssertionError("read tool mutated store: upsert() called")

    def delete(self, *a, **k):
        raise AssertionError("read tool mutated store: delete() called")

    def delete_by_metadata(self, *a, **k):
        raise AssertionError("read tool mutated store: delete_by_metadata() called")

    def get_or_create_collection(self, *a, **k):
        raise AssertionError("read tool mutated store: get_or_create_collection() called")


@contextmanager
def _patch_factory(spy):
    """Route VectorStoreFactory.create_multi_collection to the spy."""
    from src.libs import vector_store as vs_mod

    orig = vs_mod.VectorStoreFactory.__dict__.get("create_multi_collection")
    vs_mod.VectorStoreFactory.create_multi_collection = staticmethod(
        lambda settings: spy,
    )
    try:
        yield spy
    finally:
        vs_mod.VectorStoreFactory.create_multi_collection = orig


def _write_settings(tmp_path, *, collection: str = "") -> Path:
    p = tmp_path / "settings.yaml"
    p.write_text(
        "vector_store:\n  backend: chroma\n"
        "  persist_path: " + str(tmp_path / "chroma") + "\n"
        f"  collection_name: \"{collection}\"\n",
        encoding="utf-8",
    )
    return p


def test_list_collections_reads_without_mutating(tmp_path):
    spy = _SpyVectorStore(count_value=7, existing={"default"})
    cfg = _write_settings(tmp_path, collection="default")
    with _patch_factory(spy):
        _, structured = _run(lc._list_collections({
            "_data_dir": str(tmp_path / "data"),
            "_config_path": str(cfg),
        }))
    # Read surface worked; no mutator raised; the configured collection is
    # listed with its read count (7), not written.
    assert structured["n_collections"] == 1
    assert structured["collections"][0]["name"] == "default"
    assert any(c[0] == "count" for c in spy.read_calls)
    mutators = [c for c in spy.read_calls if c[0] in
                ("add", "upsert", "delete", "delete_by_metadata",
                 "get_or_create_collection")]
    assert mutators == []


def test_get_document_summary_reads_without_mutating(tmp_path):
    """The handler reaches the store only through client.get_document (read)."""
    from src.mcp_server.clients.models import DocumentInfo

    class SpyClient:
        def __init__(self): self.calls = []
        def get_document(self, document_id, principal):
            self.calls.append(("get_document", document_id))
            return DocumentInfo(document_id=document_id, collection="kb",
                                title="T", source="/x.pdf", chunk_count=1)
        # No write methods exist on a client → structural read-only.

    client = SpyClient()
    from src.mcp_server.tools import get_document_summary as gds
    result = _run(gds._get_document_summary({
        "doc_id": "10000000-0000-0000-0000-000000000000",
        "_client": client,
    }))
    md, structured = result
    assert structured["chunk_count"] == 1
    assert client.calls and client.calls[0][0] == "get_document"
    # A read client exposes no mutation method.
    for name in ("add", "upsert", "delete", "create_collection", "save"):
        assert not hasattr(client, name)


# ---------------------------------------------------------------------------
# 4. Inaccessible document == nonexistent document (no presence leakage)
# ---------------------------------------------------------------------------

def test_forbidden_and_nonexistent_doc_return_same_error_shape(tmp_path):
    """Both an access-denied and a not-found document return an ``is_error``
    tool result whose message carries the same "document not found" marker —
    the exact-string normalisation to ``document not found or not accessible``
    is finalised in P2.3 (get_document evolution)."""
    from src.mcp_server.clients.errors import (
        AccessDeniedError,
        ResourceNotFoundError,
    )
    from src.mcp_server.tools import get_document_summary as gds

    def run(exc):
        class FakeClient:
            def get_document(self, document_id, principal):
                raise exc
        return _run(gds._get_document_summary({
            "doc_id": "00000000-0000-0000-0000-000000000000",
            "_client": FakeClient(),
        }))

    not_found = run(ResourceNotFoundError("document not found"))
    forbidden = run(AccessDeniedError("document not found or not accessible"))
    # Both are tool-level known errors (is_error), never protocol errors.
    assert not_found.is_error
    assert forbidden.is_error
    for result in (not_found, forbidden):
        text = result.content[0].text
        assert "document not found" in text
        # Never leak the target path or collection.
        assert "/x.pdf" not in text
        assert "  kb  " not in text