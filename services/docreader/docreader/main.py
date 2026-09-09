# SPDX-License-Identifier: MIT
#
# Trimmed-migration from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/main.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# Removed for this project: auth/TLS interceptors, MinIO/cloud persistence,
# request-id threading context, and the URL path (web_parser is deferred).
# Kept: gRPC DocReader servicer with unary Read + streaming ReadStream, engine
# list, streaming image frames, and (optional) health checking. The standard
# gRPC health package is used only when importable (grpcio-health-checking).
"""
SKDY DocReader gRPC service entrypoint.

Local-only service: converts file bytes -> Markdown (+ image frames). The main
service is solely responsible for image persistence, chunking, and indexing.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import traceback
import uuid
from concurrent import futures
from typing import Iterator

import grpc

from docreader.config import CONFIG
from docreader.parser import registry
from docreader.parser.parser import Parser
from docreader.proto import docreader_pb2, docreader_pb2_grpc

logger = logging.getLogger(__name__)

_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def to_valid_utf8_text(s: str | None) -> str:
    if not s:
        return ""
    s = _SURROGATE_RE.sub("\ufffd", s)
    return s.encode("utf-8", errors="replace").decode("utf-8")


_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _mime_for_ref(ref_path: str) -> tuple[str, str]:
    """Return (filename, mime_type) for an image reference path."""
    fname = os.path.basename(ref_path) or f"{uuid.uuid4().hex}.png"
    ext = os.path.splitext(fname)[1].lower()
    return fname, _MIME_MAP.get(ext, "application/octet-stream")


def _iter_image_frames(images: dict[str, bytes]) -> Iterator[docreader_pb2.ImageRef]:
    """Yield one ImageRef per image, freeing each source entry as we stream."""
    for ref_path in list(images.keys()):
        data = images.pop(ref_path)
        fname, mime = _mime_for_ref(ref_path)
        yield docreader_pb2.ImageRef(
            filename=fname,
            original_ref=ref_path,
            mime_type=mime,
            image_data=data,
        )


class DocReaderServicer(docreader_pb2_grpc.DocReaderServicer):
    """gRPC DocReader service (local parse only)."""

    def __init__(self, parser: Parser | None = None) -> None:
        super().__init__()
        self.parser = parser or Parser()

    def _parse_request(self, request: docreader_pb2.ReadRequest):
        cfg = request.config
        parser_engine = cfg.parser_engine if cfg else ""
        overrides = dict(cfg.parser_engine_overrides) if cfg else {}
        file_type = request.file_type or os.path.splitext(request.file_name)[1][1:]
        return self.parser.parse_file(
            request.file_name, file_type, request.file_content,
            parser_engine=parser_engine, engine_overrides=overrides,
        )

    def Read(self, request, context):
        try:
            result = self._parse_request(request)
        except Exception as exc:  # noqa: BLE001
            logger.error("Read failed: %s", exc)
            return docreader_pb2.ReadResponse(error=str(exc))
        if not result or not result.content:
            return docreader_pb2.ReadResponse(error=f"Failed to parse: {request.file_name}")
        image_refs = []
        for ref in list(result.images.keys()):
            fname, mime = _mime_for_ref(ref)
            image_refs.append(
                docreader_pb2.ImageRef(
                    filename=fname, original_ref=ref, mime_type=mime,
                    image_data=result.images[ref],
                ),
            )
        return docreader_pb2.ReadResponse(
            markdown_content=to_valid_utf8_text(result.content),
            image_refs=image_refs,
            metadata={k: str(v) for k, v in result.metadata.items()},
        )

    def ReadStream(self, request, context):
        try:
            result = self._parse_request(request)
        except Exception as exc:  # noqa: BLE001
            logger.error("ReadStream failed: %s", exc)
            yield docreader_pb2.ReadStreamResponse(
                meta=docreader_pb2.ReadStreamMeta(error=str(exc)),
            )
            return
        if not result or not result.content:
            yield docreader_pb2.ReadStreamResponse(
                meta=docreader_pb2.ReadStreamMeta(
                    error=f"Failed to parse: {request.file_name}",
                ),
            )
            return

        images = dict(result.images)
        yield docreader_pb2.ReadStreamResponse(
            meta=docreader_pb2.ReadStreamMeta(
                markdown_content=to_valid_utf8_text(result.content),
                image_dir_path="",
                metadata={k: str(v) for k, v in result.metadata.items()},
                image_count=len(images),
            ),
        )
        sent = 0
        for frame in _iter_image_frames(images):
            yield docreader_pb2.ReadStreamResponse(image=frame)
            sent += 1
        logger.info("ReadStream sent content + %d images", sent)

    def ListEngines(self, request, context):
        engines = registry.list_engines(overrides=None)
        return docreader_pb2.ListEnginesResponse(
            engines=[
                docreader_pb2.ParserEngineInfo(
                    name=e["name"],
                    description=e.get("description", ""),
                    file_types=e.get("file_types", []),
                    available=e.get("available", True),
                    unavailable_reason=e.get("unavailable_reason", ""),
                )
                for e in engines
            ],
        )


def _configure_logging() -> None:
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    logging.root.addHandler(handler)
    logging.root.setLevel((os.environ.get("LOG_LEVEL") or "INFO").upper())


def _add_health(server: grpc.Server) -> None:
    """Add the gRPC health service when grpcio-health-checking is present."""
    try:
        from grpc_health.v1 import health_pb2_grpc
        from grpc_health.v1.health import HealthServicer
    except ImportError:
        logger.warning("grpcio-health-checking not installed; health service skipped")
        return
    hs = HealthServicer()
    hs.set("", "SERVING")
    health_pb2_grpc.add_HealthServicer_to_server(hs, server)


def serve() -> None:
    _configure_logging()
    CONFIG.print_config()
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=CONFIG.grpc_max_workers),
        options=[
            ("grpc.max_send_message_length", CONFIG.grpc_max_message_bytes),
            ("grpc.max_receive_message_length", CONFIG.grpc_max_message_bytes),
        ],
    )
    docreader_pb2_grpc.add_DocReaderServicer_to_server(DocReaderServicer(), server)
    if CONFIG.enable_health:
        _add_health(server)
    server.add_insecure_port(f"[::]:{CONFIG.grpc_port}")
    server.start()
    logger.info("SKDY DocReader serving on port %d (insecure/local)", CONFIG.grpc_port)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down DocReader")
        server.stop(0)


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())