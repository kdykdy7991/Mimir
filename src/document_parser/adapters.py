"""Migration adapters (plan §5.3 / §12.2).

Two helpers smooth the transition without ever replacing the stable legacy
Loader chain wholesale:

- :class:`LegacyLoaderParserAdapter` — implements the new :class:`DocumentParser`
  interface on top of an existing ``BaseLoader``. It materialises the
  ``ParseRequest.content`` bytes to a controlled temp file, runs the loader,
  and maps the loader's ``Document`` to a ``ParsedDocument`` *in memory*
  (image bytes are read back so the unified image-persistence path has a single
  source of truth).
- :class:`ParsedDocumentAdapter` — the canonical ``ParsedDocument -> Document``
  mapping that **both** legacy and DocReader outputs must route through, so the
  ingestion pipeline only ever sees one Document shape.

Security notes:
- File names are ``basename()``-sanitised (plan §11); the upload name is never
  used as a writable temp path.
- No command execution, no remote access.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from pathlib import Path

from src.core.types import Document, ImageRef
from src.document_parser.base import DocumentParser, ParserEngineInfo
from src.document_parser.errors import UnsupportedFormatError
from src.document_parser.types import (
    ParseRequest,
    ParseStatus,
    ParsedDocument,
    ParsedImage,
)
from src.libs.loader.base_loader import BaseLoader, LoaderError

# Truncation length to match the legacy loaders' ``Document.id`` shape.
DOC_HASH_LEN = 16


def sanitize_file_name(name: str) -> str:
    """Return a safe basename with only the base + single extension.

    Drops path separators and a leading dot so an upload like
    ``../../[evil].pdf`` becomes ``[evil].pdf`` (plan §11).
    """
    base = os.path.basename(name.replace("\\", "/"))
    stem, dot, ext = base.rpartition(".")
    if not stem:
        # e.g. ".gitignore" or "hidden" — strip a leading dot entirely.
        return ext.lstrip(".") if not stem else ext
    return f"{stem}{dot}{ext}"


def guess_mime(filename: str) -> str:
    """Best-effort MIME for an image filename, falls back to octet-stream."""
    return mimetypes.guess_type(filename, strict=False)[0] or "application/octet-stream"


class LegacyLoaderParserAdapter(DocumentParser):
    """Adapt a legacy ``BaseLoader`` to the unified ``DocumentParser`` protocol."""

    def __init__(
        self,
        loader: BaseLoader,
        *,
        supported_formats: tuple[str, ...] = (),
        engine_name: str = "legacy",
    ) -> None:
        self._loader = loader
        self._formats = supported_formats or tuple(
            getattr(loader, "supported_extensions", lambda: ())(),
        )
        self._engine_name = engine_name

    # -- DocumentParser ----------------------------------------------------
    def parse(self, request: ParseRequest) -> ParsedDocument:
        name = sanitize_file_name(request.file_name)
        if not Path(name).suffix:
            raise UnsupportedFormatError(
                f"Cannot infer format for {request.file_name!r}; "
                f"file_type must be joint extension+MIME+magic.",
            )
        try:
            with tempfile.TemporaryDirectory(prefix="skdy-parse-") as tmp:
                tmp_path = Path(tmp) / name
                tmp_path.write_bytes(request.content)
                doc = self._loader.load(str(tmp_path))
        except LoaderError as exc:
            raise UnsupportedFormatError(str(exc)) from exc

        return self._document_to_parsed(doc, request=request)

    def list_engines(self) -> list[ParserEngineInfo]:
        return [
            ParserEngineInfo(
                name=self._engine_name,
                supported_formats=tuple(self._formats),
            ),
        ]

    # -- mapping helpers ---------------------------------------------------
    def _document_to_parsed(
        self,
        doc: Document,
        *,
        request: ParseRequest,
    ) -> ParsedDocument:
        images: list[ParsedImage] = []
        for ref in doc.images:
            try:
                data = Path(ref.path).read_bytes()
            except OSError:
                data = b""
            if not data:
                continue
            images.append(
                ParsedImage(
                    filename=Path(ref.path).name,
                    original_ref=str(ref.id),
                    mime_type=guess_mime(ref.path),
                    data=data,
                    page=ref.page,
                    is_original=True,
                ),
            )

        metadata: dict = dict(doc.metadata)
        metadata.setdefault("doc_type", doc.metadata.get("doc_type", "unknown"))
        metadata.setdefault("source_path", request.source_path)
        metadata["parser_engine"] = self._engine_name
        metadata["parse_status"] = ParseStatus.SUCCESS.value
        return ParsedDocument(
            markdown=doc.text,
            images=images,
            metadata=metadata,
            parse_status=ParseStatus.SUCCESS,
        )


class ParsedDocumentAdapter:
    """Map a :class:`ParsedDocument` onto the existing :class:`core.types.Document`.

    This is the single choke point both legacy and DocReader outputs flow
    through (plan §5.3), so chunking / image registration only ever reasons
    about one shape.
    """

    def __init__(self, *, id_length: int = DOC_HASH_LEN) -> None:
        self._id_length = id_length

    def to_document(self, parsed: ParsedDocument) -> Document:
        metadata: dict = dict(parsed.metadata)
        # Preserve the parser's partial-success diagnostics through chunking
        # and vector persistence so the read-only document detail API can
        # expose real warnings instead of reconstructing or inventing them.
        metadata["parse_status"] = parsed.parse_status.value
        if parsed.warnings:
            metadata["parse_warnings"] = list(parsed.warnings)
        doc_id = metadata.pop("doc_id", None) or self._hash_id(
            parsed.markdown, metadata.get("source_path", ""),
        )
        document = Document(id=doc_id, text=parsed.markdown, metadata=metadata)

        # Images: raw bytes ride along on the ImageRef from load to the
        # pipeline's Stage 2.5, where they are persisted to ImageStorage and
        # ``path`` becomes a real file. We keep bytes here because the docreader
        # transport produces byte payloads (not pre-written files); dropping them
        # here (as the plan's stub ``image_persistence`` intended to avoid) made
        # embedded/scanned images unusable downstream.
        refs: list[ImageRef] = []
        for i, img in enumerate(parsed.images):
            refs.append(
                ImageRef(
                    id=img.original_ref or f"{doc_id}_{i}",
                    path=img.filename,  # provisional; rewritten on persistence
                    page=img.page,
                    text_offset=0,
                    text_length=0,
                    position=None,
                    is_content=True,
                    classification_reason="parsed_image",
                    data=img.data,
                    mime_type=img.mime_type,
                ),
            )
        if refs:
            metadata["images"] = [r.to_dict() for r in refs]
        document.metadata = metadata
        return document

    @staticmethod
    def _hash_id(*parts: str) -> str:
        h = hashlib.sha256("|".join(parts).encode("utf-8"))
        return h.hexdigest()[:DOC_HASH_LEN]
