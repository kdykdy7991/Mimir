"""
Multimodal assembler — turns retrieval results into a flat list
of MCP content blocks (``mcp.types.TextContent`` /
``mcp.types.ImageContent``).

Responsibilities
----------------
* Walk each :class:`RetrievalResult` and emit one
  :class:`TextContent` followed by zero-or-more
  :class:`ImageContent` (one per ``image_refs`` entry).
* Resolve each ``image_id`` against :class:`ImageStorage` and
  load the bytes for base64 encoding.
* Raise :class:`ImageNotFoundError` when an ``image_id`` is not
  in storage — never silently skip, so the caller knows a chunk
  references a missing image.

What this module deliberately does NOT do
----------------------------------------
* No OCR / captioning (that is the ``ImageCaptioner`` transform's
  job, in the ingestion phase).
* No embedding (done upstream in the pipeline).
* No HTTP URL synthesis (no Web consumer today).
* No chunk re-ranking (that is the ``RerankerStage``'s job).

The assembler's only IO is a single ``ImageStorage.get`` call
per referenced image; everything else is in-memory shaping.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from mcp.types import ImageContent as MCPImageContent
from mcp.types import TextContent as MCPTextContent

from src.core.types import RetrievalResult
from src.ingestion.storage.image_storage import (
    ImageRecord,
    ImageStorage,
)


class ImageNotFoundError(KeyError):
    """
    Raised when a chunk's ``image_refs`` contains an id that is
    not present in the :class:`ImageStorage` index.

    Subclasses ``KeyError`` so generic ``except KeyError`` blocks
    still catch it, but exposes a dedicated type for tests and
    upstream error mapping.
    """

    def __init__(self, image_id: str) -> None:
        super().__init__(image_id)
        self.image_id = image_id

    def __str__(self) -> str:
        return f"image not found in storage: {self.image_id!r}"


# Project-internal aliases — see :mod:`src.core.response.content_block`
# for the rationale. These names keep the rest of the project from
# importing ``mcp.types`` directly.
ContentBlock = MCPTextContent | MCPImageContent
TextContent = MCPTextContent
ImageContent = MCPImageContent


class MultimodalAssembler:
    """
    Build a list of MCP content blocks from a list of
    :class:`RetrievalResult`.

    Usage::

        assembler = MultimodalAssembler(image_storage)
        blocks = assembler.assemble(results)
        # blocks == [TextContent(...), ImageContent(...), ...]

    Parameters
    ----------
    image_storage:
        Source of truth for image bytes. The assembler resolves
        each ``image_refs`` id through this storage; the
        assembler itself does no filesystem IO beyond what
        ``image_storage`` exposes.
    """

    def __init__(self, image_storage: ImageStorage) -> None:
        self._image_storage = image_storage

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def assemble(
        self, results: list[RetrievalResult],
    ) -> list[ContentBlock]:
        """
        Walk ``results`` and produce a flat block list.

        For each result, emits one :class:`TextContent` followed
        by one :class:`ImageContent` per ``image_refs`` entry
        (in declaration order).

        A result without ``image_refs`` (or with an empty list)
        produces a single :class:`TextContent`. The ``text``
        field may be empty for image-only chunks in the future
        (not used today, but the shape supports it).
        """
        blocks: list[ContentBlock] = []
        for result in results:
            blocks.append(TextContent(type="text", text=result.text or ""))
            image_refs = self._extract_image_refs(result)
            for image_id in image_refs:
                blocks.append(self._build_image_block(image_id))
        return blocks

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_image_refs(result: RetrievalResult) -> list[str]:
        """
        Pull ``image_refs`` out of the result's metadata.

        The chunk schema stores the list under
        ``metadata["image_refs"]`` (see ``DocumentChunker`` and
        ``ImageCaptioner``). We accept any iterable of strings
        and preserve order; anything else yields an empty list
        rather than raising, since a malformed ref list should
        not blow up a successful retrieval.
        """
        meta = result.metadata or {}
        raw = meta.get("image_refs") or []
        if not isinstance(raw, (list, tuple)):
            return []
        return [r for r in raw if isinstance(r, str)]

    def _build_image_block(self, image_id: str) -> ImageContent:
        """
        Resolve ``image_id`` via :class:`ImageStorage` and build
        an MCP :class:`ImageContent`.

        Raises
        ------
        ImageNotFoundError
            if the storage has no record for ``image_id``.
        """
        record = self._image_storage.get(image_id)
        if record is None:
            raise ImageNotFoundError(image_id)
        raw_bytes = self._read_bytes(record)
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return ImageContent(
            type="image",
            data=encoded,
            mimeType=_guess_mime(record),
        )

    @staticmethod
    def _read_bytes(record: ImageRecord) -> bytes:
        """Read the on-disk file pointed to by ``record.file_path``."""
        return Path(record.file_path).read_bytes()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guess_mime(record: ImageRecord) -> str:
    """
    Best-effort mime type from the file extension.

    Falls back to ``image/png`` — which is what
    :class:`PdfLoader` writes — when the extension is unknown.
    The MCP spec requires a non-empty ``mimeType`` so we never
    return ``None``.
    """
    mime, _ = mimetypes.guess_type(record.file_path)
    return mime or "image/png"


__all__ = [
    "MultimodalAssembler",
    "ImageNotFoundError",
    "ContentBlock",
    "TextContent",
    "ImageContent",
]
