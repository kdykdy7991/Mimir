"""Tests for migration adapters (plan §5.3 / §12.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.types import Document, ImageRef
from src.document_parser.adapters import (
    LegacyLoaderParserAdapter,
    ParsedDocumentAdapter,
    guess_mime,
    sanitize_file_name,
)
from src.document_parser.types import (
    ParseRequest,
    ParseStatus,
    ParsedDocument,
    ParsedImage,
)
from src.libs.loader.base_loader import BaseLoader, LoaderError


class FakeLoader(BaseLoader):
    """Deterministic loader that returns a fixed Document with one image."""

    image_bytes = b"\x89PNG-fake"

    def __init__(self, *, images: list[ImageRef] | None = None) -> None:
        self._images = images or []

    def load(self, path: str) -> Document:
        if not Path(path).is_file():
            raise LoaderError(f"missing {path}")
        self.last_path = path
        return Document(
            id="fakeid1234567890",
            text="hello page",
            metadata={
                "source_path": path,
                "doc_type": "pdf",
                "images": [i.to_dict() for i in self._images],
            },
        )

    @staticmethod
    def supported_extensions() -> frozenset[str]:
        return frozenset({".pdf", ".md"})


# --- sanitize / mime --------------------------------------------------------
def test_sanitize_basename() -> None:
    assert sanitize_file_name("../../[evil].pdf") == "[evil].pdf"
    assert sanitize_file_name("dir\\\\file.md") == "file.md"
    assert sanitize_file_name("plain.pdf") == "plain.pdf"
    assert sanitize_file_name(".hidden") == "hidden"
    assert sanitize_file_name("report.v1.docx") == "report.v1.docx"


def test_guess_mime() -> None:
    assert guess_mime("a.png") == "image/png"
    assert guess_mime("a.pdf") == "application/pdf"
    assert guess_mime("a.unknownext") == "application/octet-stream"


# --- LegacyLoaderParserAdapter ---------------------------------------------
def test_adapter_maps_loader_document_and_images(tmp_path: Path) -> None:
    img_file = tmp_path / "shot.png"
    img_file.write_bytes(b"\x89PNG-raw-bytes")
    ref = ImageRef(
        id="doc_0_0", path=str(img_file), page=2, is_content=True,
    )
    adapter = LegacyLoaderParserAdapter(FakeLoader(images=[ref]))

    parsed = adapter.parse(
        ParseRequest(
            source_path="/uploads/a.pdf",
            file_name="a.pdf",
            file_type="pdf",
            content=b"%PDF-fake-content",
        ),
    )
    assert parsed.markdown == "hello page"
    assert parsed.metadata["doc_type"] == "pdf"
    assert parsed.metadata["parser_engine"] == "legacy"
    assert parsed.parse_status is ParseStatus.SUCCESS
    assert len(parsed.images) == 1
    assert parsed.images[0].data == b"\x89PNG-raw-bytes"
    assert parsed.images[0].page == 2
    assert parsed.images[0].original_ref == "doc_0_0"


def test_adapter_rejects_no_extension() -> None:
    adapter = LegacyLoaderParserAdapter(FakeLoader())
    with pytest.raises(Exception) as e:
        adapter.parse(
            ParseRequest(source_path="x", file_name="README", content=b"hi", file_type=""),
        )
    assert "format" in str(e.value).lower()


def test_adapter_list_engines() -> None:
    adapter = LegacyLoaderParserAdapter(FakeLoader(), engine_name="legacy")
    info = adapter.list_engines()
    assert info[0].name == "legacy"
    assert set(info[0].supported_formats) >= {".pdf", ".md"}


# --- ParsedDocumentAdapter --------------------------------------------------
def test_parsed_document_to_document() -> None:
    parsed = ParsedDocument(
        markdown="# t\nhello",
        metadata={"source_path": "/x/a.pdf", "doc_type": "pdf"},
        warnings=["page 2 OCR confidence is low"],
        parse_status=ParseStatus.PARTIAL_SUCCESS,
        images=[
            ParsedImage(
                filename="p1.png", original_ref="img-1", mime_type="image/png",
                data=b"IMG", page=1,
            ),
        ],
    )
    doc = ParsedDocumentAdapter().to_document(parsed)
    assert doc.text == "# t\nhello"
    assert doc.metadata["doc_type"] == "pdf"
    assert doc.metadata["parse_status"] == "partial_success"
    assert doc.metadata["parse_warnings"] == ["page 2 OCR confidence is low"]
    assert doc.metadata["images"][0]["id"] == "img-1"
    assert doc.id  # deterministic hash
    # id deterministic for same source+markdown
    again = ParsedDocumentAdapter().to_document(parsed)
    assert again.id == doc.id


def test_parsed_document_to_document_respects_explicit_id() -> None:
    parsed = ParsedDocument(
        markdown="m", metadata={"source_path": "/x.pdf", "doc_id": "explicit1"},
    )
    doc = ParsedDocumentAdapter().to_document(parsed)
    assert doc.id == "explicit1"
    assert "doc_id" not in doc.metadata  # popped into id
