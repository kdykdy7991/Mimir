"""DocReader streaming client (plan §8 Phase 1; §12.2 tests).

This module is deliberately independent of the generated protobuf stubs so it
can be unit-tested with a fake transport and imported without pulling in gRPC.
A :class:`DocReaderTransport` abstracts the wire: the real pb2-backed transport
(implemented with the DocReader service) is wired up in the composition layer
later, converting ``ParseRequest`` -> ``ReadRequest`` and stub responses ->
:class:`StreamFrame`.

Error semantics (plan §5 fault handling):
- a transport raising isn't swallowed; stream frames carrying a meta.error
  become :class:`ParseFailedError` with the attempt chain preserved.
- an empty/interrupted stream without a meta header is treated as a failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol

from src.document_parser.base import ParserEngineInfo
from src.document_parser.errors import ParseFailedError
from src.document_parser.types import (
    ParseRequest,
    ParseStatus,
    ParsedDocument,
    ParsedImage,
)


@dataclass
class ReadStreamMeta:
    """The single metadata header emitted before any image frame."""

    markdown: str
    metadata: dict[str, object] = field(default_factory=dict)
    error: str | None = None
    image_count: int = 0


@dataclass
class StreamFrame:
    """One frame from a ReadStream: either the meta header or one image."""

    meta: ReadStreamMeta | None = None
    image: dict | None = None

    @classmethod
    def meta_frame(cls, **kw: object) -> "StreamFrame":
        return cls(meta=ReadStreamMeta(**kw))

    @classmethod
    def image_frame(cls, **img: object) -> "StreamFrame":
        return cls(image=img)


class DocReaderTransport(Protocol):
    """Wire abstraction a DocReaderClient consumes.

    ``read_stream`` yields frames back-to-back: first the meta header, then one
    frame per image. ``timeout`` is the overall deadline for the call.
    """

    def read_stream(
        self, request: ParseRequest, timeout: float,
    ) -> Iterable[StreamFrame]: ...

    def list_engines(self, timeout: float) -> list[ParserEngineInfo]: ...

    def close(self) -> None: ...


class DocReaderClient:
    """Consume a DocReader read-stream into a :class:`ParsedDocument`."""

    def __init__(
        self,
        transport: DocReaderTransport,
        *,
        timeout: float = 300.0,
    ) -> None:
        self._transport = transport
        self._timeout = timeout

    # -- public API ----------------------------------------------------------
    def parse(self, request: ParseRequest) -> ParsedDocument:
        return self._collect(request, self._timeout)

    def list_engines(self, timeout: float | None = None) -> list[ParserEngineInfo]:
        return self._transport.list_engines(timeout if timeout is not None else self._timeout)

    def close(self) -> None:
        self._transport.close()

    # -- internals -----------------------------------------------------------
    def _collect(self, request: ParseRequest, timeout: float) -> ParsedDocument:
        meta: ReadStreamMeta | None = None
        images: list[ParsedImage] = []
        stream = self._transport.read_stream(request, timeout)
        for frame in stream:
            if frame.meta is not None:
                meta = frame.meta
                if meta.error:
                    raise ParseFailedError(
                        meta.error,
                        attempts=[{"service": "docreader", "ok": False}],
                    )
            elif frame.image is not None:
                if meta is None:
                    raise ParseFailedError(
                        "ReadStream image frame arrived before metadata header",
                    )
                image = self._to_image(frame.image)
                if image is not None:
                    images.append(image)

        if meta is None:
            raise ParseFailedError(
                "ReadStream ended without a metadata header (empty or interrupted)",
            )
        return ParsedDocument(
            markdown=meta.markdown,
            images=images,
            metadata=dict(meta.metadata),
            parse_status=ParseStatus.SUCCESS,
            warnings=[],
        )

    @staticmethod
    def _to_image(raw: dict) -> ParsedImage | None:
        data = raw.get("data")
        if not isinstance(data, (bytes, bytearray)) or not data:
            return None
        return ParsedImage(
            filename=str(raw.get("filename", "")),
            original_ref=str(raw.get("original_ref", "")),
            mime_type=str(raw.get("mime_type") or "application/octet-stream"),
            data=bytes(data),
            page=raw.get("page") if isinstance(raw.get("page"), int) else None,
            is_original=bool(raw.get("is_original", True)),
        )