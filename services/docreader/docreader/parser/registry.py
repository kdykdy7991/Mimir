# SPDX-License-Identifier: MIT
#
# Trimmed-migration from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/parser/registry.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# Per provenance: cloud engines (MinerU / remote hybrid) are removed; only a
# local builtin set is registered. Engines are NOT eagerly imported (plan §6:
# avoid import-time loading of every optional dependency). Format parsers are
# added as their phases land; PlainTextParser (txt/md) is registered now so the
# service is runnable and health-probeable in Phase 1.
"""Engine registry: format -> parser engine, plus engine advertisement."""

from __future__ import annotations

import logging
from typing import Any, Dict

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)

# format (lowercased, no dot) -> factory callable. Lazy so optional deps are
# only imported when a parser is actually requested.
_ENGINES: Dict[str, dict] = {}


def _plain_text() -> BaseParser:
    from docreader.parser.text_parser import PlainTextParser
    return PlainTextParser()


def register_engine(fmt: str, *, name: str, factory, description: str = "") -> None:
    """Register an engine for ``fmt`` (lowercased, no leading dot)."""
    _ENGINES[fmt.lstrip(".").lower()] = {
        "name": name,
        "description": description,
        "factory": factory,
    }


def _default_engines() -> None:
    if not _ENGINES:
        register_engine("txt", name="builtin", factory=_plain_text,
                        description="Plain text pass-through")
        register_engine("md", name="builtin", factory=_plain_text,
                        description="Markdown pass-through")
        register_engine("markdown", name="builtin", factory=_plain_text,
                        description="Markdown pass-through")


def engine_for(fmt: str) -> BaseParser:
    """Instantiate the engine registered for ``fmt`` (raises on unknown)."""
    _default_engines()
    fmt = fmt.lstrip(".").lower()
    entry = _ENGINES.get(fmt)
    if entry is None:
        raise ValueError(f"unsupported file type: {fmt!r}")
    return entry["factory"]()


def list_engines(overrides: Dict[str, Any] | None = None) -> list[dict]:
    """Advertise available engines (name/description/file_types/available)."""
    _default_engines()
    by_name: Dict[str, dict] = {}
    for fmt, entry in _ENGINES.items():
        info = by_name.setdefault(
            entry["name"],
            {
                "name": entry["name"],
                "description": entry["description"],
                "file_types": [],
                "available": True,
                "unavailable_reason": "",
            },
        )
        info["file_types"].append(fmt)
    by_name[entry["name"]]["file_types"].sort()
    return list(by_name.values())


def parse_file(
    file_name: str, file_type: str, content: bytes, *,
    parser_engine: str | None = None, engine_overrides: Dict[str, str] | None = None,
) -> Document:
    """Parse file bytes using the engine for ``file_type`` (or explicit engine)."""
    fmt = (file_type or (file_name.rsplit(".", 1)[-1] if "." in file_name else "")).lower()
    engine = engine_for(fmt)
    logger.info("parse_file: engine=%s fmt=%s", engine.__class__.__name__, fmt)
    return engine.parse(content)