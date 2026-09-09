# SPDX-License-Identifier: MIT
#
# Adapted from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/parser/chain_parser.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# Basic-migration. Two required adaptations per docs/plan §6 / provenance:
#   1) FirstParser must NOT swallow the final error — on total failure it
#      raises ChainParseError carrying the full attempt chain, instead of
#      silently returning an empty Document.
#   2) Only the used ``endecode.encode_bytes`` helper is inlined (the upstream
#      endecode module pulls numpy/PIL we don't need yet) — attibution kept.
"""
Chain Parser Module

Two chain-of-responsibility implementations for document parsing:
1. FirstParser   — tries parsers sequentially until one succeeds.
2. PipelineParser — chains parsers where each consumes the previous output.
"""

import logging
from typing import Dict, Tuple, Type

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# Minimal equivalent of upstream ``utils.endecode.encode_bytes``.
def _encode_bytes(content: str) -> bytes:
    return content.encode("utf-8")


class ChainParseError(RuntimeError):
    """Raised when a parser chain exhausts all attempts without success.

    ``attempts`` records each engine's outcome for observability (plan §6/§14).
    """

    def __init__(self, message: str, attempts: list[dict]) -> None:
        super().__init__(message)
        self.attempts = attempts


class FirstParser(BaseParser):
    """
    First-success parser that tries multiple parsers in sequence.

    Returns the result of the first parser that yields a valid document. If
    every parser fails, raises :class:`ChainParseError` (adaptation: upstream
    returned an empty Document, which would hide the root cause).
    """

    # Tuple of parser classes to be instantiated
    _parser_cls: Tuple[Type["BaseParser"], ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._parsers: list[BaseParser] = [
            parser_cls(*args, **kwargs) for parser_cls in self._parser_cls
        ]

    def parse_into_text(self, content: bytes) -> Document:
        attempts: list[dict] = []
        for p in self._parsers:
            logger.info("FirstParser: using parser %s", p.__class__.__name__)
            try:
                document = p.parse_into_text(content)
            except Exception as exc:  # noqa: BLE001
                attempts.append({"engine": p.__class__.__name__, "ok": False, "error": str(exc)})
                logger.exception(
                    "FirstParser: parser %s raised; trying next",
                    p.__class__.__name__,
                )
                continue

            if document.is_valid():
                logger.info("FirstParser: parser %s succeeded", p.__class__.__name__)
                return document
            attempts.append({"engine": p.__class__.__name__, "ok": False, "error": "invalid/empty document"})

        last_error = attempts[-1]["error"] if attempts else "no parsers configured"
        raise ChainParseError(
            f"FirstParser: all parsers failed. Last: {last_error}",
            attempts=attempts,
        )

    @classmethod
    def create(cls, *parser_classes: Type["BaseParser"]) -> Type["FirstParser"]:
        names = "_".join(p.__name__ for p in parser_classes)
        return type(f"FirstParser_{names}", (cls,), {"_parser_cls": parser_classes})


class PipelineParser(BaseParser):
    """Chain parsers; each stage consumes the previous stage's output."""

    _parser_cls: Tuple[Type["BaseParser"], ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._parsers: list[BaseParser] = [
            parser_cls(*args, **kwargs) for parser_cls in self._parser_cls
        ]

    def parse_into_text(self, content: bytes) -> Document:
        images: Dict[str, bytes] = {}
        metadata: Dict = {}
        document = Document()
        for p in self._parsers:
            logger.info("PipelineParser: using parser %s", p.__class__.__name__)
            document = p.parse_into_text(content)
            content = _encode_bytes(document.content)
            images.update(document.images)
            metadata.update(document.metadata)
        document = Document(
            content=document.content,
            images=images,
            metadata=metadata,
        )
        return document

    @classmethod
    def create(cls, *parser_classes: Type["BaseParser"]) -> Type["PipelineParser"]:
        names = "_".join(p.__name__ for p in parser_classes)
        return type(f"PipelineParser_{names}", (cls,), {"_parser_cls": parser_classes})