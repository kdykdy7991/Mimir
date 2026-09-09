"""Tests for the pymupdf-backed PDFParser router (scanned / text / hybrid /
embedded figures), ported from WeKnora pdf_parser behaviour."""

from __future__ import annotations

import io

import pymupdf
from PIL import Image as PILImage

from docreader.parser.pdf_parser import PDFParser


def _jpeg_bytes(size=(120, 120)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", size, (80, 120, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _text_pdf(pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for p in range(pages):
        page = doc.new_page(width=400, height=600)
        page.insert_text((50, 100), f"Native text page {p + 1}")
        page.insert_text((50, 130), "some longer body sentence that is well formed.")
    data = doc.tobytes()
    doc.close()
    return data


def _scanned_pdf(pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for p in range(pages):
        page = doc.new_page(width=400, height=600)
        page.insert_image(pymupdf.Rect(20, 20, 380, 580), stream=_jpeg_bytes())
    data = doc.tobytes()
    doc.close()
    return data


def _embedded_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text((50, 100), "Embedded figure page text content here.")
    page.insert_image(pymupdf.Rect(60, 300, 360, 500), stream=_jpeg_bytes((200, 200)))
    data = doc.tobytes()
    doc.close()
    return data


def test_text_pdf_uses_text_layer() -> None:
    doc = PDFParser(file_name="a.pdf").parse_into_text(_text_pdf())
    assert "Native text page 1" in doc.content
    assert doc.metadata["image_source_type"] == "pdf_text_layer"
    assert doc.metadata["scanned_page_count"] == 0


def test_scanned_pdf_renders_page_image() -> None:
    doc = PDFParser(file_name="scan.pdf").parse_into_text(_scanned_pdf())
    assert doc.metadata["scanned_page_count"] == 1
    assert doc.metadata["image_source_type"] == "scanned_pdf"
    assert len(doc.images) == 1
    key = next(iter(doc.images))
    assert key.endswith(".jpg")
    # raw bytes are a decodable JPEG
    assert PILImage.open(io.BytesIO(doc.images[key])).format == "JPEG"
    assert f"![scan_page_1.jpg]({key})" in doc.content


def test_hybrid_routes_per_page() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text((50, 100), "hybrid native page")
    page = doc.new_page(width=400, height=600)
    page.insert_image(pymupdf.Rect(20, 20, 380, 580), stream=_jpeg_bytes())
    data = doc.tobytes()
    doc.close()

    result = PDFParser(file_name="hyb.pdf").parse_into_text(data)
    assert result.metadata["page_count"] == 2
    assert result.metadata["scanned_page_count"] == 1
    assert result.metadata["text_page_count"] == 1
    assert "hybrid native page" in result.content
    assert len(result.images) == 1


def test_embedded_figure_extracted() -> None:
    doc = PDFParser(file_name="emb.pdf").parse_into_text(_embedded_pdf())
    assert doc.metadata["embedded_image_count"] == 1
    image_keys = [k for k in doc.images if "img_" in k]
    assert len(image_keys) == 1
    assert f"![{image_keys[0].split('/')[-1]}]({image_keys[0]})" in doc.content


def test_force_scanned_override_routes_all_pages() -> None:
    doc = PDFParser(file_name="f.pdf", pdf_force_scanned="1").parse_into_text(_text_pdf())
    assert doc.metadata["scanned_page_count"] == 1
    assert doc.metadata["text_page_count"] == 0


def test_parse_via_registry() -> None:
    from docreader.parser import registry
    doc = registry.parse_file("a.pdf", "pdf", _text_pdf())
    assert "Native text page 1" in doc.content