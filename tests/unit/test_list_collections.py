"""
P1.2 routing: ``list_collections`` through ``InProcessRagReadOnlyClient``.

Covers:
- BM25 indices listed with chunk counts
- Empty data dir / empty configured name → friendly empty-state
- Config without a settings file → defaults applied
- Vector counts patched → reported
- Authorization filtering by the principal
- Tool registration + thin-handler formatting via a fake client
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from src.mcp_server.auth.context import TrustedLocalPrincipal
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient
from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import list_collections as lc


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _client(tmp_path: Path, *, cfg_path: Path | None = None):
    return InProcessRagReadOnlyClient(
        config_path=str(cfg_path) if cfg_path else str(tmp_path / "no.yaml"),
        data_dir=str(tmp_path),
    )


def _write_bm25(data_dir: Path, name: str, docs: list[str]) -> None:
    bm25_dir = data_dir / "db" / "bm25"
    bm25_dir.mkdir(parents=True, exist_ok=True)
    (bm25_dir / f"{name}.json").write_text(
        json.dumps({"docs": docs, "version": 1}),
        encoding="utf-8",
    )


def _write_settings(tmp_path: Path, *, collection: str = "default") -> Path:
    settings = f"""
vector_store:
  backend: chroma
  persist_path: {tmp_path}/db/chroma
  collection_name: "{collection}"

retrieval:
  sparse_backend: bm25
  top_k_dense: 20
  top_k_sparse: 20
  top_k_final: 10
"""
    p = tmp_path / "settings.yaml"
    p.write_text(settings, encoding="utf-8")
    return p


def test_lists_bm25_collections(tmp_path: Path):
    _write_bm25(tmp_path, "alpha", ["a", "b", "c"])
    _write_bm25(tmp_path, "beta", ["x"])
    cfg = _write_settings(tmp_path, collection="alpha")

    collections = _client(tmp_path, cfg_path=cfg).list_collections(TrustedLocalPrincipal())

    names = [c.name for c in collections]
    assert "alpha" in names
    assert "beta" in names
    alpha = next(c for c in collections if c.name == "alpha")
    assert alpha.bm25_chunks == 3
    assert alpha.source in ("both", "bm25")


def test_collection_description_is_exposed(tmp_path: Path):
    _write_bm25(tmp_path, "skdy common", ["company profile"])
    cfg = _write_settings(tmp_path, collection="skdy common")
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + "\nmcp:\n  collection_descriptions:\n    \"skdy common\": 时空道宇公司的综合知识库\n",
        encoding="utf-8",
    )
    client = _client(tmp_path, cfg_path=cfg)
    collections = client.list_collections(TrustedLocalPrincipal())
    item = collections[0]
    assert item.name == "skdy common"
    assert item.description == "时空道宇公司的综合知识库"

    md, structured = _run(_call_handler(client))
    entry = structured["collections"][0]
    assert entry["description"] == "时空道宇公司的综合知识库"
    assert "时空道宇公司的综合知识库" in md


def _call_handler(client):
    return lc._list_collections({"_client": client, "_data_dir": ".", "_config_path": "."})


def test_marks_overlap_as_both(tmp_path: Path):
    _write_bm25(tmp_path, "shared", ["d1"])
    cfg = _write_settings(tmp_path, collection="shared")
    client = _client(tmp_path, cfg_path=cfg)
    with patch.object(client, "_vector_counts", return_value={"shared": 7}):
        collections = client.list_collections(TrustedLocalPrincipal())
    shared = next(c for c in collections if c.name == "shared")
    assert shared.source == "both"
    assert shared.vector_count == 7
    assert shared.bm25_chunks == 1


def test_only_bm25_collections_appear(tmp_path: Path):
    _write_bm25(tmp_path, "x", ["a"])
    cfg = _write_settings(tmp_path, collection="y")  # configured but not on disk
    client = _client(tmp_path, cfg_path=cfg)
    with patch.object(client, "_vector_counts", return_value={"x": 0, "y": 0}):
        collections = client.list_collections(TrustedLocalPrincipal())
    names = [c.name for c in collections]
    assert names == ["x", "y"]
    src_by_name = {c.name: c.source for c in collections}
    assert src_by_name["x"] == "bm25"
    assert src_by_name["y"] == "vector_store"


def test_n_docs_bm25_format_counts_chunks(tmp_path: Path):
    bm25_dir = tmp_path / "db" / "bm25"
    bm25_dir.mkdir(parents=True, exist_ok=True)
    (bm25_dir / "modern.json").write_text(
        json.dumps({"n_docs": 42, "avgdl": 5.5, "k1": 1.2, "b": 0.75,
                    "terms": {}}),
        encoding="utf-8",
    )
    _write_bm25(tmp_path, "legacy", ["x"])
    cfg = _write_settings(tmp_path, collection="other")
    client = _client(tmp_path, cfg_path=cfg)
    with patch.object(client, "_vector_counts", return_value={}):
        collections = client.list_collections(TrustedLocalPrincipal())
    by_name = {c.name: c for c in collections}
    assert by_name["modern"].bm25_chunks == 42
    assert by_name["legacy"].bm25_chunks == 1


def test_empty_data_dir_returns_friendly_markdown(tmp_path: Path):
    cfg = _write_settings(tmp_path, collection="")
    client = _client(tmp_path, cfg_path=cfg)
    with patch.object(client, "_vector_counts", return_value={}):
        md, structured = _run(_call_handler(client))
    assert structured["n_collections"] == 0
    assert "No collections found" in md


def test_missing_data_dir_does_not_raise(tmp_path: Path):
    cfg = _write_settings(tmp_path, collection="default")
    client = InProcessRagReadOnlyClient(
        config_path=str(cfg), data_dir=str(tmp_path / "does-not-exist"),
    )
    with patch.object(client, "_vector_counts", return_value={"default": 0}):
        collections = client.list_collections(TrustedLocalPrincipal())
    assert len(collections) == 1


def test_corrupt_bm25_file_is_skipped(tmp_path: Path):
    bm25_dir = tmp_path / "db" / "bm25"
    bm25_dir.mkdir(parents=True, exist_ok=True)
    (bm25_dir / "broken.json").write_text("{not valid json", encoding="utf-8")
    _write_bm25(tmp_path, "good", ["a"])
    cfg = _write_settings(tmp_path, collection="default")
    client = _client(tmp_path, cfg_path=cfg)
    with patch.object(client, "_vector_counts", return_value={}):
        collections = client.list_collections(TrustedLocalPrincipal())
    names = [c.name for c in collections]
    assert "good" in names
    assert "broken" not in names


def test_authorization_filters_collections(tmp_path: Path):
    _write_bm25(tmp_path, "public", ["a"])
    _write_bm25(tmp_path, "secret", ["b"])
    cfg = _write_settings(tmp_path, collection="")
    client = _client(tmp_path, cfg_path=cfg)
    with patch.object(client, "_vector_counts", return_value={}):
        collections = client.list_collections(
            _restricted_principal({"public"}),
        )
    assert [c.name for c in collections] == ["public"]


def _restricted_principal(allowed: set[str]):
    class P:
        key_id = "k"
        name = "n"
        allowed_collections = frozenset(allowed)
    return P()


# ---------------------------------------------------------------------------
# Registration + thin-handler formatting
# ---------------------------------------------------------------------------

def test_register_adds_tool_to_handler():
    h = ProtocolHandler()
    lc.register(h)
    assert h.has("list_collections")


def test_handler_dispatches_via_fake_client():
    """The handler only formats; the client does the work."""
    from src.mcp_server.clients.models import CollectionInfo

    class FakeClient:
        def __init__(self): self.calls = 0
        def list_collections(self, principal):
            self.calls += 1
            return [CollectionInfo(name="a", source="bm25")]

    fake = FakeClient()
    md, structured = _run(lc._list_collections({"_client": fake}))
    assert fake.calls == 1
    assert structured["n_collections"] == 1
    assert structured["collections"][0]["name"] == "a"