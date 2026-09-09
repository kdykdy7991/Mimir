# SPDX-License-Identifier: MIT
#
# Adapted from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/config.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# Trimmed to local-only knobs (no cloud/storage). Invalid values fail fast at
# startup (plan §9: invalid config fails at boot, not mid-parse).
"""DocReader service configuration (local-only, validated at startup)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    return int(raw.strip())


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class DocReaderConfig:
    grpc_port: int
    grpc_max_workers: int
    grpc_max_message_bytes: int
    log_level: str
    enable_health: bool
    pdf_render_max_workers: int
    pdf_render_parallelism: int
    pdf_render_dpi: int
    pdf_jpeg_quality: int
    pdf_render_max_edge: int

    @classmethod
    def from_env(cls) -> "DocReaderConfig":
        cfg = cls(
            grpc_port=_int("DOCREADER_GRPC_PORT", 50051),
            grpc_max_workers=_int("DOCREADER_GRPC_MAX_WORKERS", 4),
            grpc_max_message_bytes=_int(
                "DOCREADER_GRPC_MAX_MESSAGE_BYTES",
                64 * 1024 * 1024,
            ),
            log_level=(os.environ.get("LOG_LEVEL") or "INFO").upper(),
            enable_health=_bool("DOCREADER_ENABLE_HEALTH", True),
            pdf_render_max_workers=_int("DOCREADER_PDF_RENDER_MAX_WORKERS", 1),
            pdf_render_parallelism=_int(
                "DOCREADER_PDF_RENDER_PARALLELISM",
                max(1, min(4, os.cpu_count() or 1)),
            ),
            pdf_render_dpi=_int("DOCREADER_PDF_RENDER_DPI", 200),
            pdf_jpeg_quality=_int("DOCREADER_PDF_JPEG_QUALITY", 85),
            pdf_render_max_edge=_int("DOCREADER_PDF_RENDER_MAX_EDGE", 2000),
        )
        # Fail fast on invalid config (plan §9).
        if cfg.grpc_port <= 0 or cfg.grpc_port > 65535:
            raise ValueError(f"invalid grpc_port: {cfg.grpc_port}")
        if cfg.grpc_max_workers <= 0:
            raise ValueError(f"invalid grpc_max_workers: {cfg.grpc_max_workers}")
        if cfg.grpc_max_message_bytes <= 0:
            raise ValueError(f"invalid grpc_max_message_bytes: {cfg.grpc_max_message_bytes}")
        if cfg.pdf_render_max_workers <= 0:
            raise ValueError(f"invalid pdf_render_max_workers: {cfg.pdf_render_max_workers}")
        if cfg.pdf_render_dpi <= 0:
            raise ValueError(f"invalid pdf_render_dpi: {cfg.pdf_render_dpi}")
        if cfg.pdf_jpeg_quality < 1 or cfg.pdf_jpeg_quality > 100:
            raise ValueError(f"invalid pdf_jpeg_quality: {cfg.pdf_jpeg_quality}")
        if cfg.pdf_render_max_edge <= 0:
            raise ValueError(f"invalid pdf_render_max_edge: {cfg.pdf_render_max_edge}")
        return cfg

    def print_config(self) -> None:
        logger.info(
            "DocReader config: port=%d workers=%d max_msg=%d bytes health=%s "
            "pdf(dpi=%d quality=%d max_edge=%d par=%d)",
            self.grpc_port,
            self.grpc_max_workers,
            self.grpc_max_message_bytes,
            self.enable_health,
            self.pdf_render_dpi,
            self.pdf_jpeg_quality,
            self.pdf_render_max_edge,
            self.pdf_render_parallelism,
        )


CONFIG = DocReaderConfig.from_env()