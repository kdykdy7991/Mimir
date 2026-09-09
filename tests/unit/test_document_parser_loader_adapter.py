"""Tests for the BaseLoader bridge over a DocumentParser (plan §8 Phase 1)."""

from __future__ import annotations

from pathlib import Path

from src.document_parser.adapters import LegacyLoaderParserAdapter
from src.document_parser.loader_adapter import DocumentParserLoader
from src.libs.loader.base_loader import LoaderError
from src.libs.loader.markdown_loader import MarkdownLoader


def _md_loader() -> DocumentParserLoader:
    return DocumentParserLoader(
        LegacyLoaderParserAdapter(MarkdownLoader(), supported_formats=(".md",)),
    )


def test_load_roundtrip_via_legacy_adapter(tmp_path: Path) -> None:
    p = tmp_path / "note.md"
    p.write_text("# Title\n\nSome *markdown* body.\n", encoding="utf-8")
    loader = _md_loader()
    doc = loader.load(str(p))
    assert doc.text == "# Title\n\nSome *markdown* body.\n"
    assert doc.metadata["parser_engine"] == "legacy"


def test_missing_file_raises_loader_error(tmp_path: Path) -> None:
    loader = _md_loader()
    try:
        loader.load(str(tmp_path / "nope.md"))
    except Exception as exc:
        assert isinstance(exc, LoaderError)
    else:
        raise AssertionError("expected LoaderError")


def test_supported_extensions_from_engine() -> None:
    loader = _md_loader()
    exts = loader.supported_extensions()
    assert ".md" in exts