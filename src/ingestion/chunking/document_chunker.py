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
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from src.core.types import Chunk, Document, ImageRef
from src.libs.loader.pdf_loader import extract_image_mentions
from src.libs.splitter.base_splitter import BaseSplitter


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
    def split_document(self, document: Document) -> list[Chunk]:
        """
        Split a Document into Chunks with full metadata wiring.

        Returns an empty list if the document has no text.
        """
        if not document.text:
            return []

        raw_texts = self.splitter.split_text(document.text)
        if not raw_texts:
            return []

        # Pre-resolve Document.images once (every chunk may need it).
        doc_images: list[ImageRef] = document.images

        chunks: list[Chunk] = []
        running_offset = 0
        for index, chunk_text in enumerate(raw_texts):
            start, end = self._find_offsets(
                document.text, chunk_text, running_offset
            )
            chunk = self._build_chunk(
                document=document,
                doc_images=doc_images,
                index=index,
                text=chunk_text,
                start_offset=start,
                end_offset=end,
            )
            chunks.append(chunk)
            running_offset = end
        return chunks

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
    ) -> Chunk:
        chunk_id = self._generate_chunk_id(document.id, index, text)
        chunk_meta = self._inherit_metadata(
            document=document,
            chunk_index=index,
            chunk_text=text,
            doc_images=doc_images,
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
    ) -> dict[str, Any]:
        """
        Build the chunk's metadata dict.

        1. Shallow-copy Document.metadata so every chunk carries the
           same top-level fields (source_path, doc_type, page_count…).
        2. Add ``chunk_index``.
        3. If the chunk text mentions any ``[IMAGE: id]`` placeholders
           and the parent document has matching ``ImageRef``s, set
           ``images`` (filtered list) and ``image_refs`` (id list).
           Otherwise the chunk gets no ``images`` key — downstream
           consumers check key presence to avoid inheriting the full
           document-level image set.
        """
        chunk_meta: dict[str, Any] = dict(document.metadata)
        # Strip the document-level ``images`` list — chunks get a
        # per-chunk filtered copy only when they reference at least
        # one image (see step 3 below). Per spec, chunks with no
        # placeholders MUST NOT carry the full document images list;
        # otherwise downstream C7 ImageCaptioner would not be able to
        # tell which images belong to which chunk.
        chunk_meta.pop("images", None)
        chunk_meta["chunk_index"] = chunk_index

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
