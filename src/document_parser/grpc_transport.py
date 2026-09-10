"""Real gRPC transport for :class:`DocReaderClient` (plan §8 / §Phase 1).

This is the wire implementation of :class:`DocReaderTransport` against the
DocReader service's generated stubs. It is deliberately separated from
``client.py`` (which is stub-free for unit tests). The proto imports happen
inside methods so that importing this module never fails on a host without the
service package or gRPC; construction raises a clear error instead.
"""

from __future__ import annotations

from typing import Any, Iterable

from src.document_parser.base import ParserEngineInfo
from src.document_parser.client import StreamFrame
from src.document_parser.types import ParseRequest


class DocReaderGrpcTransport:
    """Stream ReadStream / ListEngines frames over the DocReader gRPC service."""

    def __init__(self, endpoint: str, timeout: float = 300.0) -> None:
        import grpc  # noqa: PLC0415
        pb, pb_grpc = _load_proto()
        self._pb2 = pb
        self._grpc = grpc
        self._endpoint = endpoint
        self._timeout = timeout
        self._channel = grpc.insecure_channel(endpoint)
        self._stub = pb_grpc.DocReaderStub(self._channel)

    def read_stream(self, request: ParseRequest, timeout: float) -> Iterable[StreamFrame]:
        pb = self._pb2.ReadRequest(
            file_content=request.content,
            file_name=request.file_name or "",
            file_type=request.file_type or "",
        )
        try:
            for frame in self._stub.ReadStream(pb, timeout=timeout or self._timeout):
                yield self._to_frame(frame)
        except self._grpc.RpcError as exc:
            raise ConnectionError(f"docreader read stream failed: {exc}") from exc

    def list_engines(self, timeout: float | None = None) -> list[ParserEngineInfo]:
        try:
            resp = self._stub.ListEngines(
                self._pb2.ListEnginesRequest(), timeout=timeout or self._timeout,
            )
        except self._grpc.RpcError as exc:
            raise ConnectionError(f"docreader list_engines failed: {exc}") from exc
        out = []
        for e in resp.engines:
            out.append(ParserEngineInfo(
                name=e.name,
                supported_formats=tuple(e.file_types),
                available=bool(e.available),
                unavailable_reason=e.unavailable_reason or None,
            ))
        return out

    def close(self) -> None:
        try:
            self._channel.close()
        except Exception:  # noqa: BLE001
            pass

    def _to_frame(self, raw: Any) -> StreamFrame:
        if raw.HasField("meta"):
            m = raw.meta
            return StreamFrame.meta_frame(
                markdown=m.markdown_content,
                metadata=dict(m.metadata),
                error=m.error or None,
                image_count=int(m.image_count),
            )
        if raw.HasField("image") and raw.image.image_data:
            im = raw.image
            return StreamFrame.image_frame(
                data=im.image_data,
                filename=im.filename,
                original_ref=im.original_ref,
                mime_type=im.mime_type,
            )
        return StreamFrame()


def _load_proto():
    """Import the generated DocReader proto stubs, tolerating either layout."""
    import importlib  # noqa: PLC0415
    for base in ("services.docreader.docreader.proto", "docreader.proto"):
        try:
            pb = importlib.import_module(base + ".docreader_pb2")
            pb_grpc = importlib.import_module(base + ".docreader_pb2_grpc")
            return pb, pb_grpc
        except ImportError:
            continue
    raise ImportError(
        "DocReader proto stubs unavailable; ensure the docreader service "
        "package is importable",
    )