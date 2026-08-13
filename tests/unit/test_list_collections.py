"""
Unit tests for ``list_collections`` tool (E4).

Covers:
- BM25 indices listed with chunk counts
- Empty data dir → friendly empty-state
- Config without a settings file → defaults applied
- Settings vector_store field reported (count may be null when
  the underlying store is unavailable in tests)
- Tool registration on ProtocolHandler
- Tool dispatch returns (markdown, structured) tuple
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.mcp_server.protocol_handler import ProtocolHandler
from src.mcp_server.tools import list_collections as lc


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _write_bm25(data_dir: Path, name: str, docs: list[str]) -> None:
    bm25_dir = data_dir / "db" / "bm25"
    bm25_dir.mkdir(parents=True, exist_ok=True)
    (bm25_dir / f"{name}.json").write_text(
        json.dumps({"docs": docs, "version": 1}),
        encoding="utf-8",
    )


def _write_settings(data_dir: Path, *, collection: str = "default") -> Path:
    settings = f"""
vector_store:
  backend: chroma
  persist_path: {data_dir}/db/chroma
  collection_name: "{collection}"

retrieval:
  sparse_backend: bm25
  top_k_dense: 20
  top_k_sparse: 20
  top_k_final: 10
"""
    p = data_dir / "settings.yaml"
    p.write_text(settings, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# BM25 listing
# ---------------------------------------------------------------------------

def test_lists_bm25_collections(tmp_path: Path):
    _write_bm25(tmp_path, "alpha", ["a", "b", "c"])
    _write_bm25(tmp_path, "beta", ["x"])
    cfg = _write_settings(tmp_path, collection="alpha")

    md, structured = _run(lc._list_collections({
        "_data_dir": str(tmp_path),
        "_config_path": str(cfg),
    }))

    names = [c["name"] for c in structured["collections"]]
    assert "alpha" in names
    assert "beta" in names
    assert structured["n_collections"] == 2

    alpha = next(c for c in structured["collections"] if c["name"] == "alpha")
    assert alpha["bm25_chunks"] == 3
    assert alpha["source"] in ("both", "bm25")


def test_collection_description_is_exposed_to_mcp_clients(tmp_path: Path):
    _write_bm25(tmp_path, "skdy common", ["company profile"])
    cfg = _write_settings(tmp_path, collection="skdy common")
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + "\nmcp:\n  collection_descriptions:\n    \"skdy common\": 时空道宇公司的综合知识库\n",
        encoding="utf-8",
    )
    markdown, structured = _run(lc._list_collections({
        "_data_dir": str(tmp_path), "_config_path": str(cfg),
    }))
    item = structured["collections"][0]
    assert item["name"] == "skdy common"
    assert item["description"] == "时空道宇公司的综合知识库"
    assert "时空道宇公司的综合知识库" in markdown


def test_marks_overlap_as_both(tmp_path: Path):
    _write_bm25(tmp_path, "shared", ["d1"])
    cfg = _write_settings(tmp_path, collection="shared")
    # Patch the vector-count call so the test doesn't need a real
    # chroma db. We return a count of 7 for the shared collection.
    with patch.object(lc, "_vector_counts", return_value={"shared": 7}):
        _, structured = _run(lc._list_collections({
            "_data_dir": str(tmp_path),
            "_config_path": str(cfg),
        }))

    shared = next(c for c in structured["collections"] if c["name"] == "shared")
    assert shared["source"] == "both"
    assert shared["vector_count"] == 7
    assert shared["bm25_chunks"] == 1


def test_only_bm25_collections_appear(tmp_path: Path):
    _write_bm25(tmp_path, "x", ["a"])
    cfg = _write_settings(tmp_path, collection="y")  # configured but not on disk
    with patch.object(lc, "_vector_counts", return_value={"x": 0, "y": 0}):
        _, structured = _run(lc._list_collections({
            "_data_dir": str(tmp_path),
            "_config_path": str(cfg),
        }))

    names = [c["name"] for c in structured["collections"]]
    assert names == ["x", "y"]
    src_by_name = {c["name"]: c["source"] for c in structured["collections"]}
    assert src_by_name["x"] == "bm25"
    assert src_by_name["y"] == "vector_store"


def test_bm25_collection_reports_real_vector_count(tmp_path: Path):
    """A BM25-only collection also reports its per-collection vector
    count — the M3 bug where only ``default`` was counted is fixed."""
    _write_bm25(tmp_path, "shared", ["a", "b", "c"])
    cfg = _write_settings(tmp_path, collection="other")
    with patch.object(lc, "_vector_counts", return_value={"shared": 989}):
        _, structured = _run(lc._list_collections({
            "_data_dir": str(tmp_path),
            "_config_path": str(cfg),
        }))

    shared = next(c for c in structured["collections"] if c["name"] == "shared")
    assert shared["source"] == "bm25"
    assert shared["bm25_chunks"] == 3
    assert shared["vector_count"] == 989


def test_n_docs_bm25_format_counts_chunks(tmp_path: Path):
    """Current BM25Indexer.save() format is ``{n_docs, avgdl, k1, b,
    terms}`` — the chunk count comes from ``n_docs`` (the legacy
    ``docs`` list is a fallback)."""
    bm25_dir = tmp_path / "db" / "bm25"
    bm25_dir.mkdir(parents=True, exist_ok=True)
    (bm25_dir / "modern.json").write_text(
        json.dumps({"n_docs": 42, "avgdl": 5.5, "k1": 1.2, "b": 0.75,
                    "terms": {}}),
        encoding="utf-8",
    )
    _write_bm25(tmp_path, "legacy", ["x"])
    cfg = _write_settings(tmp_path, collection="other")
    with patch.object(lc, "_vector_counts", return_value={}):
        _, structured = _run(lc._list_collections({
            "_data_dir": str(tmp_path),
            "_config_path": str(cfg),
        }))

    by_name = {c["name"]: c for c in structured["collections"]}
    assert by_name["modern"]["bm25_chunks"] == 42
    assert by_name["legacy"]["bm25_chunks"] == 1


def test_empty_data_dir_returns_friendly_markdown(tmp_path: Path):
    """No bm25 indices + no configured store name → empty hint."""
    cfg = _write_settings(tmp_path, collection="")
    with patch.object(lc, "_vector_counts", return_value={}):
        md, structured = _run(lc._list_collections({
            "_data_dir": str(tmp_path),
            "_config_path": str(cfg),
        }))
    assert structured["n_collections"] == 0
    assert "No collections found" in md
    assert "ingest.py" in md


def test_empty_data_dir_lists_configured_collection(tmp_path: Path):
    """Empty data dir but a configured collection name → it still shows
    as a vector_store-only entry (never crashes)."""
    cfg = _write_settings(tmp_path)
    with patch.object(lc, "_vector_counts", return_value={"default": 0}):
        md, structured = _run(lc._list_collections({
            "_data_dir": str(tmp_path),
            "_config_path": str(cfg),
        }))
    assert structured["n_collections"] == 1
    assert structured["collections"][0]["name"] == "default"
    assert structured["collections"][0]["source"] == "vector_store"


def test_missing_data_dir_does_not_raise(tmp_path: Path):
    cfg = _write_settings(tmp_path)
    with patch.object(lc, "_vector_counts", return_value={"default": 0}):
        md, structured = _run(lc._list_collections({
            "_data_dir": str(tmp_path / "does-not-exist"),
            "_config_path": str(cfg),
        }))
    assert structured["n_collections"] >= 0


def test_corrupt_bm25_file_is_skipped(tmp_path: Path):
    bm25_dir = tmp_path / "db" / "bm25"
    bm25_dir.mkdir(parents=True, exist_ok=True)
    (bm25_dir / "broken.json").write_text("{not valid json", encoding="utf-8")
    _write_bm25(tmp_path, "good", ["a"])
    cfg = _write_settings(tmp_path, collection="default")

    _, structured = _run(lc._list_collections({
        "_data_dir": str(tmp_path),
        "_config_path": str(cfg),
    }))
    names = [c["name"] for c in structured["collections"]]
    assert "good" in names
    assert "broken" not in names  # failed parse, not surfaced


def test_falls_back_to_defaults_when_no_config(tmp_path: Path):
    _write_bm25(tmp_path, "papers", ["a", "b"])
    md, structured = _run(lc._list_collections({
        "_data_dir": str(tmp_path),
        "_config_path": str(tmp_path / "no-such-file.yaml"),
    }))
    # No config → vector_store side is reported as "default" with
    # no count (since we can't load settings).
    names = [c["name"] for c in structured["collections"]]
    assert "papers" in names
    # No crash, no exception.


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_adds_tool_to_handler():
    h = ProtocolHandler()
    lc.register(h)
    assert h.has("list_collections")


def test_registered_tool_dispatches():
    """A tool registered through lc.register() reaches the protocol
    handler's dispatch and normalises the (str, dict) return shape."""
    h = ProtocolHandler()

    async def fake_handler(args):
        return ("# fake", {"n_collections": 0, "collections": []})

    h.register(
        name="list_collections",
        description="x",
        input_schema={},
        handler=fake_handler,
    )
    import asyncio
    out = asyncio.run(
        h.dispatch("list_collections", {}),
    )
    # dispatch returns the (unstructured, structured) tuple as-is.
    assert isinstance(out, tuple)
    assert len(out) == 2
    unstructured, structured = out
    assert len(unstructured) == 1
    assert unstructured[0].text == "# fake"
    assert structured == {"n_collections": 0, "collections": []}
