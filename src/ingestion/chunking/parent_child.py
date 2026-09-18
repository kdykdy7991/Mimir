"""Deterministic one-level structure-aware parent chunk generation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from src.application.contracts import (
    INDEX_FORMAT_VERSION,
    content_version,
    stable_parent_chunk_id,
)
from src.core.types import Chunk


@dataclass(frozen=True)
class ParentChildChunks:
    children: tuple[Chunk, ...]
    parents: tuple[Chunk, ...]


class ParentChunkBuilder:
    """Build one parent layer while leaving only children retrievable."""

    def __init__(self, *, max_parent_chars: int = 8_000) -> None:
        if isinstance(max_parent_chars, bool) or max_parent_chars < 1:
            raise ValueError("max_parent_chars must be positive")
        self.max_parent_chars = max_parent_chars

    def build(self, children: list[Chunk] | tuple[Chunk, ...]) -> ParentChildChunks:
        rows = list(children)
        if not rows:
            return ParentChildChunks((), ())
        document_id = str(rows[0].source_ref or "")
        if not document_id:
            raise ValueError("child source_ref is required for parent generation")
        document_version = str(
            rows[0].metadata.get("document_version")
            or content_version(document_id, *(row.text for row in rows))
        )
        groups = self._groups(rows)
        updated: list[Chunk] = []
        parents: list[Chunk] = []
        for ordinal, group in enumerate(groups):
            parent_id = stable_parent_chunk_id(document_id, document_version, ordinal)
            text = "\n\n".join(row.text for row in group)
            start = min(row.start_offset for row in group)
            end = max(row.end_offset for row in group)
            first_meta = dict(group[0].metadata)
            headings = self._heading_path(first_meta)
            asset_ids = tuple(dict.fromkeys(
                str(asset)
                for row in group
                for asset in (row.metadata.get("asset_ids") or ())
            ))
            parent_meta: dict[str, Any] = {
                key: value for key, value in first_meta.items()
                if key not in {
                    "chunk_index", "chunk_version", "parent_chunk_id",
                    "parent_id", "images", "image_refs",
                }
            }
            parent_meta.update({
                "index_format_version": INDEX_FORMAT_VERSION,
                "chunk_level": "parent",
                "parent_chunk_id": None,
                "document_version": document_version,
                "chunk_version": content_version(document_version, parent_id, text),
                "heading_path": list(headings),
                "source_span": {"start": start, "end": end},
                "asset_ids": list(asset_ids),
                "child_chunk_ids": [row.id for row in group],
                # Storage/index adapters use this explicit flag to keep
                # parents out of the default retrieval namespace.
                "retrievable": False,
            })
            parents.append(Chunk(
                id=parent_id, text=text, metadata=parent_meta,
                start_offset=start, end_offset=end, source_ref=document_id,
            ))
            for row in group:
                metadata = dict(row.metadata)
                metadata.update({
                    "index_format_version": INDEX_FORMAT_VERSION,
                    "chunk_level": "child",
                    "parent_chunk_id": parent_id,
                    "document_version": document_version,
                    "chunk_version": str(
                        metadata.get("chunk_version")
                        or content_version(
                            document_version,
                            str(metadata.get("chunk_index", row.id)), row.text,
                        )
                    ),
                    "heading_path": list(self._heading_path(metadata)),
                    "source_span": metadata.get("source_span") or {
                        "start": row.start_offset, "end": row.end_offset,
                    },
                })
                updated.append(replace(row, metadata=metadata))
        return ParentChildChunks(tuple(updated), tuple(parents))

    def _groups(self, rows: list[Chunk]) -> list[list[Chunk]]:
        groups: list[list[Chunk]] = []
        current: list[Chunk] = []
        current_key: tuple[Any, ...] | None = None
        current_chars = 0
        for row in rows:
            key = self._structure_key(row)
            addition = len(row.text) + (2 if current else 0)
            if current and (key != current_key or current_chars + addition > self.max_parent_chars):
                groups.append(current)
                current, current_chars = [], 0
            current.append(row)
            current_key = key
            current_chars += addition
        if current:
            groups.append(current)
        return groups

    @staticmethod
    def _heading_path(metadata: dict[str, Any]) -> tuple[str, ...]:
        value = metadata.get("heading_path") or ()
        if isinstance(value, str):
            value = (value,)
        if not value:
            heading = metadata.get("heading") or metadata.get("section")
            value = (heading,) if heading else ()
        return tuple(str(item) for item in value if str(item).strip())

    @classmethod
    def _structure_key(cls, row: Chunk) -> tuple[Any, ...]:
        meta = row.metadata
        if meta.get("content_type") == "table":
            return ("table", meta.get("table_index", meta.get("chunk_index")))
        doc_type = str(meta.get("doc_type") or "").lower()
        headings = cls._heading_path(meta)
        if doc_type in {"md", "markdown", "docx"} and headings:
            return ("heading", *headings)
        if doc_type in {"ppt", "pptx"}:
            return ("slide", meta.get("slide_num", meta.get("page_num")))
        if doc_type == "pdf":
            if headings:
                return ("heading", *headings)
            page = meta.get("page_num", meta.get("page"))
            try:
                return ("pdf-page-group", (int(page) - 1) // 2)
            except (TypeError, ValueError):
                pass
        return ("window",)


__all__ = ["ParentChildChunks", "ParentChunkBuilder"]
