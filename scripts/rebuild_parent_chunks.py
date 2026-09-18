#!/usr/bin/env python3
"""Dry-run or rebuild one document's Task-06 parent sidecar version."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.types import Chunk
from src.ingestion.chunk_order import chunk_id_of
from src.ingestion.chunking import ParentChunkBuilder
from src.ingestion.storage import ParentChunkStore
from src.mcp_server.clients.in_process import InProcessRagReadOnlyClient


def rebuild(
    *, document_id: str, data_dir: str, config: str, dry_run: bool,
) -> dict:
    client = InProcessRagReadOnlyClient(data_dir=data_dir, config_path=config)
    resolved = client._resolve_doc(document_id)  # migration-only storage adapter
    if resolved is None:
        raise RuntimeError("document not found")
    collection, source_path = resolved
    hits = client._ordered_chunks(collection, source_path)
    if not hits:
        raise RuntimeError("document has no indexed chunks")
    children = []
    for index, hit in enumerate(hits):
        metadata = dict(hit.get("metadata") or {})
        children.append(Chunk(
            id=chunk_id_of(hit), text=str(hit.get("text") or ""),
            metadata=metadata,
            start_offset=int((metadata.get("source_span") or {}).get("start", 0)),
            end_offset=int((metadata.get("source_span") or {}).get("end", 0)),
            source_ref=str(metadata.get("source_ref") or document_id),
        ))
    built = ParentChunkBuilder().build(children)
    version = str(built.children[0].metadata.get("document_version") or "legacy-v1")
    report = {
        "document_id": document_id, "collection": collection,
        "version": version, "children": len(built.children),
        "parents": len(built.parents), "dry_run": dry_run,
    }
    if not dry_run:
        store = ParentChunkStore(Path(data_dir) / "db" / "parent_chunks.db")
        store.stage(
            collection=collection, document_id=document_id, version=version,
            children=built.children, parents=built.parents,
        )
        store.activate(
            collection=collection, document_id=document_id, version=version,
        )
        report["activated"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("document_id")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--config", default="./config/settings.yaml")
    parser.add_argument("--apply", action="store_true", help="stage and activate; default is dry-run")
    args = parser.parse_args(argv)
    try:
        report = rebuild(
            document_id=args.document_id, data_dir=args.data_dir,
            config=args.config, dry_run=not args.apply,
        )
    except Exception as exc:  # noqa: BLE001 - CLI failure report
        print(json.dumps({
            "document_id": args.document_id, "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
