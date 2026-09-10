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

# (format, engine name) -> engine record. Lazy so optional deps are only
# imported when a parser is actually requested. Keying on both format and name
# lets several engines share one format (e.g. builtin vs opendataloader for pdf).
_ENGINES: Dict[tuple, dict] = {}


def _plain_text() -> BaseParser:
    from docreader.parser.text_parser import PlainTextParser
    return PlainTextParser()


def _pdf() -> BaseParser:
    from docreader.parser.pdf_parser import PDFParser
    return PDFParser()


def _reset_registry() -> None:
    _ENGINES.clear()


def register_engine(fmt: str, *, name: str, factory, description: str = "", available=None) -> None:
    """Register an engine for ``fmt`` (lowercased, no leading dot).

    ``available`` may be a zero-arg callable returning ``(bool, reason)`` used
    by ``list_engines`` to advertise runtime availability (e.g. optional JVM
    engines), or None for always-available engines.
    """
    _ENGINES[(fmt.lstrip(".").lower(), name)] = {
        "name": name,
        "description": description,
        "factory": factory,
        "available": available,
    }


def _odl() -> BaseParser:
    from docreader.parser.opendataloader_parser import OpenDataLoaderParser
    return OpenDataLoaderParser()


def _docx() -> BaseParser:
    from docreader.parser.docx_parser import DocxParser
    return DocxParser()


def _csv() -> BaseParser:
    from docreader.parser.csv_parser import CsvParser
    return CsvParser()


def _xlsx() -> BaseParser:
    from docreader.parser.xlsx_parser import XlsxParser
    return XlsxParser()


def _pptx() -> BaseParser:
    from docreader.parser.pptx_parser import PptxParser
    return PptxParser()


def _legacy_office() -> BaseParser:
    from docreader.parser.legacy_office_parser import LegacyOfficeParser
    return LegacyOfficeParser()


def _legacy_office_available():
    from docreader.parser.legacy_office_parser import legacy_office_available
    return legacy_office_available()


def _html() -> BaseParser:
    from docreader.parser.html_parser import HtmlParser
    return HtmlParser()


def _mhtml() -> BaseParser:
    from docreader.parser.mhtml_parser import MhtmlParser
    return MhtmlParser()


def _epub() -> BaseParser:
    from docreader.parser.epub_parser import EpubParser
    return EpubParser()


def _xmind() -> BaseParser:
    from docreader.parser.xmind_parser import XmindParser
    return XmindParser()


def _default_engines() -> None:
    if not _ENGINES:
        register_engine("txt", name="builtin", factory=_plain_text,
                        description="Plain text pass-through")
        register_engine("md", name="builtin", factory=_plain_text,
                        description="Markdown pass-through")
        register_engine("markdown", name="builtin", factory=_plain_text,
                        description="Markdown pass-through")
        register_engine("pdf", name="builtin", factory=_pdf,
                        description="PDF: layout-aware text / scanned routing")
        register_engine(
            "pdf", name="opendataloader", factory=_odl,
            description="PDF (local OpenDataLoader, JVM layout engine)",
            available=_odl_available,
        )
        register_engine("docx", name="builtin", factory=_docx,
                        description="DOCX: OOXML paragraphs and tables to Markdown")
        register_engine("csv", name="builtin", factory=_csv,
                        description="CSV: rows to a Markdown table")
        register_engine("xlsx", name="builtin", factory=_xlsx,
                        description="XLSX: OOXML worksheets to Markdown tables")
        register_engine("pptx", name="builtin", factory=_pptx,
                        description="PPTX: OOXML slides to Markdown text")
        for fmt in ("doc", "xls", "ppt"):
            register_engine(
                fmt, name="opendataloader", factory=_legacy_office,
                description="Legacy Office (OLE2) via optional local converter",
                available=_legacy_office_available,
            )
        for fmt in ("html", "htm"):
            register_engine(fmt, name="builtin", factory=_html,
                            description="HTML/HTM: tags to readable text")
        for fmt in ("mhtml", "mht"):
            register_engine(fmt, name="builtin", factory=_mhtml,
                            description="MHTML/MHT: MIME HTML archive to text")
        register_engine("epub", name="builtin", factory=_epub,
                        description="EPUB: zip of XHTML documents to text")
        register_engine("xmind", name="builtin", factory=_xmind,
                        description="XMind: mind-map topics to indented text")


def _odl_available():
    from docreader.parser.opendataloader_parser import opendataloader_available
    return opendataloader_available()


def engine_for(fmt: str) -> BaseParser:
    """Instantiate the default engine registered for ``fmt`` (raises on unknown)."""
    _default_engines()
    return _engine_for_fmt(fmt, None)["factory"]()


def list_engines(overrides: Dict[str, Any] | None = None) -> list[dict]:
    """Advertise available engines (name/description/file_types/available)."""
    _default_engines()
    by_name: Dict[str, dict] = {}
    for (fmt, _name), entry in _ENGINES.items():
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
        av = entry.get("available")
        if callable(av):
            ok, reason = av()
            if not ok:
                info["available"] = False
                info["unavailable_reason"] = reason
    for info in by_name.values():
        info["file_types"].sort()
    return list(by_name.values())


def _engine_for_fmt(fmt: str, parser_engine: str | None) -> dict:
    """Pick the registered engine for ``fmt``, honoring an explicit name."""
    _default_engines()
    fmt = fmt.lstrip(".").lower()
    candidates = [e for (f, _n), e in _ENGINES.items() if f == fmt]
    if not candidates:
        raise ValueError(f"unsupported file type: {fmt!r}")
    if parser_engine:
        for e in candidates:
            if e["name"] == parser_engine:
                return e
        # explicit engine requested but not registered for this format
        raise ValueError(
            f"parser_engine {parser_engine!r} not available for file type {fmt!r}",
        )
    return candidates[0]


def parse_file(
    file_name: str, file_type: str, content: bytes, *,
    parser_engine: str | None = None, engine_overrides: Dict[str, str] | None = None,
) -> Document:
    """Parse file bytes using the engine for ``file_type`` (or explicit engine)."""
    fmt = (file_type or (file_name.rsplit(".", 1)[-1] if "." in file_name else "")).lower()
    entry = _engine_for_fmt(fmt, parser_engine)
    engine = entry["factory"]()
    logger.info("parse_file: engine=%s fmt=%s", entry["name"], fmt)
    return engine.parse(content)