"""
DocumentChunker — the adapter between ``libs.splitter`` and the
ingestion pipeline.

``libs.splitter`` knows how to turn a string into a list of strings.
``DocumentChunker`` takes a :class:`core.types.Document` and produces a
list of :class:`core.types.Chunk` objects with the business logic the
ingestion pipeline needs:

1. **Chunk ID generation** — stable, deterministic
   (``{doc_id}_{index:04d}_{hash_8chars}``) so re-ingesting the same
   document yields the same IDs (helps dedup, trace correlation).
2. **Metadata inheritance** — every Document.metadata field is copied
   onto each chunk (``source_path``, ``doc_type``, etc.).
3. **chunk_index** — the chunk's 0-based position in the document.
4. **source_ref** — set to the parent Document.id, so any chunk can
   be traced back to its origin.
5. **Image reference distribution** — scan each chunk's text for
   ``[IMAGE: id]`` placeholders and copy only the matching
   ``ImageRef``s into ``chunk.metadata["images"]`` (plus an
   ``image_refs`` id-list). Chunks without placeholders do NOT get
   the ``images`` key — downstream code can use key-presence to
   cheaply decide whether a chunk has attached media.
6. **Type conversion** — output is a list of ``Chunk`` dataclasses
   that satisfy the C1 contract (serializable, fields stable).
7. **Metadata-only filtering** — discard a split whose entire content is
   a dataset ``doc_id: ...`` marker so it never reaches either index.
"""

from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from src.core.types import Chunk, Document, ImageRef
from src.libs.loader.pdf_loader import extract_image_mentions
from src.libs.splitter.base_splitter import BaseSplitter


_DOC_ID_ONLY_RE = re.compile(r"^\s*doc_id\s*:\s*\S+\s*$", re.IGNORECASE)


class ChunkerError(Exception):
    """Base exception for DocumentChunker failures."""


class DocumentChunker:
    """
    Adapter that turns a ``Document`` into a list of ``Chunk``s.

    Args:
        splitter: A ``BaseSplitter`` instance. The chunker is agnostic
            to the splitter strategy (recursive, fixed-length, …) —
            it just consumes ``List[str]`` and adds business value
            on top.
    """

    def __init__(self, splitter: BaseSplitter) -> None:
        self.splitter = splitter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def split_document(
        self, document: Document, max_protected: int | None = None,
    ) -> list[Chunk]:
        """
        Split a Document into Chunks with full metadata wiring.

        When the document contains protected table spans (GFM Markdown or HTML
        tables), a table-aware path is used: ordinary split boundaries never
        fall inside a table, small tables are atomic, large tables are split by
        complete rows with the header repeated as ``context_header``. Documents
        without tables follow the original body-only path unchanged.

        Returns an empty list if the document has no text.
        """
        if not document.text:
            return []

        from src.ingestion.chunking.table_protection import (
            DEFAULT_MAX_PROTECTED,
            find_table_spans,
            split_table,
        )

        if max_protected is None:
            max_protected = DEFAULT_MAX_PROTECTED
        spans = find_table_spans(document.text, max_protected)
        if not spans:
            return self._split_plain(document)

        # --- table-aware path -------------------------------------------------
        doc_images: list[ImageRef] = document.images
        chunks: list[Chunk] = []
        chunk_index = 0
        running_offset = 0
        cursor = 0
        for span in spans:
            # body before the table
            chunks, chunk_index, running_offset = self._emit_body_region(
                document=document,
                doc_images=doc_images,
                region=(cursor, span.start),
                chunks=chunks,
                chunk_index=chunk_index,
                running_offset=running_offset,
                space_end=span.start,
            )
            # table: atomic (single part) or row-split with repeated header
            parts = split_table(span, max_protected)
            header = span.header
            start = span.start
            for part_pos, part in enumerate(parts):
                end = start + len(part)
                extra: dict[str, Any] = {
                    "content_type": "table",
                    "table_index": span.index,
                }
                if header:
                    extra["context_header"] = header
                if len(parts) > 1:
                    extra["table_part_index"] = part_pos
                chunks.append(
                    self._build_chunk(
                        document=document,
                        doc_images=doc_images,
                        index=chunk_index,
                        text=part,
                        start_offset=start,
                        end_offset=end,
                        extra_meta=extra,
                    ),
                )
                chunk_index += 1
                running_offset = end
                start = end
            cursor = span.end
            running_offset = max(running_offset, span.end)

        # trailing body after the last table
        chunks, _, _ = self._emit_body_region(
            document=document,
            doc_images=doc_images,
            region=(cursor, len(document.text)),
            chunks=chunks,
            chunk_index=chunk_index,
            running_offset=running_offset,
            space_end=len(document.text),
        )
        return chunks

    def _split_plain(self, document: Document) -> list[Chunk]:
        """Original body-only chunking (used when no tables are present)."""
        raw_texts = [
            text
            for text in self.splitter.split_text(document.text)
            if not self._is_metadata_only(text)
        ]
        if not raw_texts:
            return []
        doc_images: list[ImageRef] = document.images
        chunks: list[Chunk] = []
        running_offset = 0
        for index, chunk_text in enumerate(raw_texts):
            start, end = self._find_offsets(
                document.text, chunk_text, running_offset,
            )
            chunks.append(
                self._build_chunk(
                    document=document,
                    doc_images=doc_images,
                    index=index,
                    text=chunk_text,
                    start_offset=start,
                    end_offset=end,
                ),
            )
            running_offset = end
        return chunks

    def _emit_body_region(
        self,
        *,
        document: Document,
        doc_images: list[ImageRef],
        region: tuple[int, int],
        chunks: list[Chunk],
        chunk_index: int,
        running_offset: int,
        space_end: int,
    ) -> tuple[list[Chunk], int, int]:
        """Emit chunks for a body region, pinned so boundaries stay in-region."""
        region_start, region_end = region
        if region_end <= region_start:
            return chunks, chunk_index, running_offset
        body = document.text[region_start:region_end]
        probe = running_offset
        for part in self.splitter.split_text(body):
            if self._is_metadata_only(part):
                continue
            # locate the part within the region (avoid crossing into a table)
            start = document.text.find(part, region_start)
            if start == -1 or start + len(part) > space_end:
                start = min(probe, space_end)
            end = start + len(part)
            if end > space_end:
                end = space_end
                part = document.text[start:end] or part
            chunks.append(
                self._build_chunk(
                    document=document,
                    doc_images=doc_images,
                    index=chunk_index,
                    text=part,
                    start_offset=start,
                    end_offset=end,
                ),
            )
            chunk_index += 1
            probe = end
            running_offset = end
        return chunks, chunk_index, running_offset

    @staticmethod
    def _is_metadata_only(text: str) -> bool:
        """Return whether a split contains only a dataset ``doc_id`` line.

        Such splits carry provenance rather than searchable content.  Drop
        only this exact shape so legitimate short chunks remain indexable.
        """
        return _DOC_ID_ONLY_RE.fullmatch(text) is not None

    # ------------------------------------------------------------------
    # Chunk construction
    # ------------------------------------------------------------------
    def _build_chunk(
        self,
        *,
        document: Document,
        doc_images: list[ImageRef],
        index: int,
        text: str,
        start_offset: int,
        end_offset: int,
        extra_meta: dict[str, Any] | None = None,
    ) -> Chunk:
        chunk_id = self._generate_chunk_id(document.id, index, text)
        chunk_meta = self._inherit_metadata(
            document=document,
            chunk_index=index,
            chunk_text=text,
            doc_images=doc_images,
            extra_meta=extra_meta,
        )
        return Chunk(
            id=chunk_id,
            text=text,
            metadata=chunk_meta,
            start_offset=start_offset,
            end_offset=end_offset,
            source_ref=document.id,
        )

    # ------------------------------------------------------------------
    # ID generation
    # ------------------------------------------------------------------
    @staticmethod
    def _generate_chunk_id(doc_id: str, index: int, text: str) -> str:
        """
        Stable, content-addressed chunk id.

        Format: ``{doc_id}_{index:04d}_{content_hash[:8]}``

        The 8-char content hash makes the id unique across re-splits
        where the same text might land in a different index.
        """
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        return f"{doc_id}_{index:04d}_{content_hash}"

    # ------------------------------------------------------------------
    # Metadata + image reference distribution
    # ------------------------------------------------------------------
    @staticmethod
    def _inherit_metadata(
        *,
        document: Document,
        chunk_index: int,
        chunk_text: str,
        doc_images: list[ImageRef],
        extra_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Build the chunk's metadata dict.

        1. Shallow-copy Document.metadata so every chunk carries the
           same top-level fields (source_path, doc_type, page_count…).
        2. Add ``chunk_index``.
        3. If ``extra_meta`` is given (e.g. table context: ``content_type``,
           ``context_header``, ``table_index``), merge it on last.
        4. If the chunk text mentions any ``[IMAGE: id]`` placeholders
           and the parent document has matching ``ImageRef``s, set
           ``images`` (filtered list) and ``image_refs`` (id list).
           Otherwise the chunk gets no ``images`` key — downstream
           consumers check key presence to avoid inheriting the full
           document-level image set.
        """
        chunk_meta: dict[str, Any] = dict(document.metadata)
        chunk_meta.pop("images", None)
        chunk_meta["chunk_index"] = chunk_index
        if extra_meta:
            chunk_meta.update(extra_meta)

        if doc_images:
            mentioned_ids = set(extract_image_mentions(chunk_text))
            if mentioned_ids:
                # ImageRef objects are not hashable — match by id field.
                by_id: dict[str, ImageRef] = {
                    img.id: img for img in doc_images if img.id
                }
                matched: list[ImageRef] = []
                for img_id in extract_image_mentions(chunk_text):
                    img = by_id.get(img_id)
                    # Skip decorative images: they are saved on disk
                    # but should not be attached to chunks/returned.
                    if img is None or not getattr(img, "is_content", True):
                        continue
                    if img not in matched:
                        matched.append(img)
                if matched:
                    # Deep-copy each ImageRef dict so later mutation
                    # of the chunk's metadata can't leak back into
                    # the parent Document's metadata.
                    chunk_meta["images"] = [
                        copy.deepcopy(img.to_dict()) for img in matched
                    ]
                    chunk_meta["image_refs"] = [img.id for img in matched]

        return chunk_meta

    # ------------------------------------------------------------------
    # Offset computation
    # ------------------------------------------------------------------
    @staticmethod
    def _find_offsets(
        full_text: str, chunk_text: str, search_from: int
    ) -> tuple[int, int]:
        """
        Locate ``chunk_text`` inside ``full_text`` starting from
        ``search_from``. Falls back to ``(search_from, search_from + len)``
        if the exact substring isn't found (which can happen when the
        splitter normalized whitespace, e.g. collapsed multiple
        newlines into one).
        """
        start = full_text.find(chunk_text, search_from)
        if start == -1:
            start = search_from
        end = start + len(chunk_text)
        return start, end
