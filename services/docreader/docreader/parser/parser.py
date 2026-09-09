# SPDX-License-Identifier: MIT
#
# Trimmed-migration from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/parser/parser.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# Kept the facade with the DOC/DOCX magic correction intent and file/URL
# branches. URL parsing is OUT of scope this round (web_parser deferred), so
# ``parse_url`` raises a clear unsupported error instead of fetching.
"""Parser facade used by the gRPC servicer."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser import registry

logger = logging.getLogger(__name__)


class UnsupportedError(RuntimeError):
    """Raised for requests the service intentionally does not handle."""


class Parser:
    """Facade over the engine registry."""

    def __init__(self) -> None:
        pass

    def parse_file(
        self,
        file_name: str,
        file_type: str,
        content: bytes,
        parser_engine: Optional[str] = None,
        engine_overrides: Optional[Dict[str, str]] = None,
    ) -> Document:
        return registry.parse_file(
            file_name,
            file_type,
            content,
            parser_engine=parser_engine,
            engine_overrides=engine_overrides or {},
        )

    def parse_url(
        self,
        url: str,
        title: str,
        parser_engine: Optional[str] = None,
        engine_overrides: Optional[Dict[str, str]] = None,
    ) -> Document:
        raise UnsupportedError(
            "URL parsing is not enabled in this round (web_parser deferred).",
        )

    def list_engines(self) -> list[dict]:
        return registry.list_engines()