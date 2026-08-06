"""
E4: ``list_collections`` tool.

Lists the collections available in the data directory, with a
small per-collection summary (chunk count, vector count, where the
data lives).

The collection set is the union of two sources:

- **BM25 indices** — every ``*.json`` file under
  ``<data_dir>/db/bm25/`` is treated as a collection (the same
  durable marker ``DocumentManager`` uses).
- **Vector store** — every name in the union is also counted
  against its per-collection Chroma store via the
  :class:`MultiCollectionVectorStore` router, so a BM25-only name
  reports its real vector count too (M3 multi-collection).

The merged result is keyed by collection name; if a collection has
both a BM25 index and a vector store entry we mark it
``source: "both"``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.core.settings import Settings, load_settings
from src.mcp_server.protocol_handler import ProtocolHandler

logger = logging.getLogger(__name__)


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "n_collections": {"type": "integer"},
        "collections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "source": {
                        "type": "string",
                        "enum": ["bm25", "vector_store", "both"],
                    },
                    "bm25_chunks": {"type": ["integer", "null"]},
                    "vector_count": {"type": ["integer", "null"]},
                    "data_dir": {"type": "string"},
                },
                "required": ["name", "source", "data_dir"],
            },
        },
    },
    "required": ["n_collections", "collections"],
}


def _list_bm25(data_dir: str) -> dict[str, dict[str, Any]]:
    """Return ``{collection_name: {bm25_chunks, data_dir}}`` from disk."""
    out: dict[str, dict[str, Any]] = {}
    bm25_dir = Path(data_dir) / "db" / "bm25"
    if not bm25_dir.is_dir():
        return out
    for path in sorted(bm25_dir.glob("*.json")):
        name = path.stem
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:  # noqa: BLE001
            # Corrupt or unreadable file — skip, don't surface
            # with bm25_chunks=0 (which would be misleading).
            logger.warning(
                "failed to read bm25 index %s: %s", path, exc,
            )
            continue
        chunks = 0
        if isinstance(payload, dict):
            # Current BM25Indexer.save() writes ``{n_docs, avgdl, k1, b,
            # terms}`` — the chunk count is ``n_docs``. Legacy (pre-M3)
            # indices carried the chunks as a ``docs`` list; keep the
            # fallback so old index files still report a real count.
            n_docs = payload.get("n_docs")
            if isinstance(n_docs, int):
                chunks = n_docs
            else:
                docs = payload.get("docs")
                if isinstance(docs, list):
                    chunks = len(docs)
        out[name] = {
            "bm25_chunks": chunks,
            "data_dir": str(bm25_dir),
        }
    return out


def _vector_counts(settings: Settings, names: list[str]) -> dict[str, Any]:
    """
    Return ``{name: vector_count}`` for each collection via the
    multi-collection router.

    Real data lives in per-collection Chroma stores (one per knowledge
    base), so counting only the configured collection — as the old
    ``_vector_settings_info`` did — underreports everything except
    ``default``. Each name is best-effort: an unreadable / missing
    collection stays ``None`` rather than a misleading ``0``.
    """
    out: dict[str, Any] = {}
    try:
        from src.libs.vector_store import VectorStoreFactory
        from src.libs.vector_store.chroma_store import chroma_collection_name

        router = VectorStoreFactory.create_multi_collection(settings.vector_store)
        # Only count collections that already exist in Chroma. Calling
        # ``count()`` on a missing name would *create* an empty collection
        # via ``get_or_create_collection`` — a listing tool shouldn't
        # mutate the store.
        existing: set[str] = set()
        client = getattr(router, "_client", None)
        if client is not None:
            try:
                existing = {c.name for c in client.list_collections()}
            except Exception as exc:  # noqa: BLE001
                logger.debug("chroma list_collections failed: %s", exc)
        for name in names:
            if chroma_collection_name(name) not in existing:
                out[name] = None
                continue
            try:
                out[name] = int(router.count(collection=name))
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "vector count unavailable for %s: %s", name, exc,
                )
                out[name] = None
    except Exception as exc:  # noqa: BLE001
        logger.debug("multi-collection vector store unavailable: %s", exc)
    return out


async def _list_collections(args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    config_path = args.get("_config_path") or "./config/settings.yaml"
    data_dir = args.get("_data_dir") or "./data"

    p = Path(config_path)
    settings = load_settings(str(p)) if p.is_file() else Settings()

    bm25 = _list_bm25(data_dir)
    configured = settings.vector_store.collection_name
    # Every known collection = BM25 index files ∪ the configured store name.
    names = sorted(set(bm25) | ({configured} if configured else set()))
    counts = _vector_counts(settings, names)

    chroma_dir = str(Path(data_dir) / "db" / "chroma")
    merged: dict[str, dict[str, Any]] = {}
    for name in names:
        in_bm25 = name in bm25
        is_configured = name == configured
        if in_bm25 and is_configured:
            source = "both"
        elif in_bm25:
            source = "bm25"
        else:
            source = "vector_store"
        entry: dict[str, Any] = {
            "name": name,
            "source": source,
            "bm25_chunks": None,
            "vector_count": counts.get(name),
            "data_dir": chroma_dir,
        }
        if in_bm25:
            entry["bm25_chunks"] = bm25[name]["bm25_chunks"]
            entry["data_dir"] = f"{bm25[name]['data_dir']} + {chroma_dir}"
        merged[name] = entry

    collections = list(merged.values())
    # Stable order: alphabetical by name.
    collections.sort(key=lambda c: c["name"])

    md_lines = [f"# Collections ({len(collections)})", ""]
    for c in collections:
        bits = [f"**{c['name']}**", f"source: {c['source']}"]
        if c.get("bm25_chunks") is not None:
            bits.append(f"bm25: {c['bm25_chunks']} chunks")
        if c.get("vector_count") is not None:
            bits.append(f"vectors: {c['vector_count']}")
        md_lines.append("- " + " · ".join(bits))
    if not collections:
        md_lines.append(
            "_No collections found. Run `python scripts/ingest.py "
            "--path <pdf> --collection <name>` to create one._"
        )
    markdown = "\n".join(md_lines)

    structured = {
        "n_collections": len(collections),
        "collections": collections,
    }
    return markdown, structured


def register(handler: ProtocolHandler) -> None:
    handler.register(
        name="list_collections",
        description=(
            "List the collections available in the local data "
            "directory, with chunk/vector counts and source paths."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_list_collections,
        output_schema=OUTPUT_SCHEMA,
    )


__all__ = ["register"]
