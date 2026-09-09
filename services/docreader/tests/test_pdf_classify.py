"""Tests for PDF page classification (ported from WeKnora pdf_parser).

Covers the pure ``classify_page`` rules and the pymupdf-backed page-image-area
ratio used by the ``scanned``/``text`` decision.
"""

from __future__ import annotations

from docreader.parser.pdf_classify import (
    classify_page,
    page_image_area_ratio,
    page_text,
)


def test_image_dominant_page_is_scanned() -> None:
    # image-area >= 0.5 regardless of text
    assert classify_page(1.0, 5000) == "scanned"
    assert classify_page(0.75, 0) == "scanned"
    assert classify_page(0.6, 100) == "scanned"


def test_low_text_with_image_is_scanned() -> None:
    # few chars (<10) but some image content (>=0.1) -> scanned
    assert classify_page(0.2, 5) == "scanned"
    assert classify_page(0.1, 9) == "scanned"


def test_text_page() -> None:
    assert classify_page(0.01, 2000) == "text"
    assert classify_page(0.0, 50) == "text"
    # low text but no image content -> text (not rendered)
    assert classify_page(0.05, 5) == "text"


def test_ratio_and_classify_on_synthetic_pages() -> None:
    import io

    import pymupdf

    def make(imgs: bool, text: str) -> bytes:
        from PIL import Image as PILImage

        doc = pymupdf.open()
        page = doc.new_page(width=300, height=400)
        if imgs:
            buf = io.BytesIO()
            PILImage.new("RGB", (120, 120), (255, 255, 255)).save(buf, format="JPEG")
            page.insert_image(pymupdf.Rect(30, 30, 270, 370), stream=buf.getvalue())
        page.insert_text((40, 40), text)
        data = doc.tobytes()
        doc.close()
        return data

    scan_pdf = pymupdf.open(stream=make(True, "hi"), filetype="pdf")
    scan_pdf_page = scan_pdf.load_page(0)
    ratio = page_image_area_ratio(scan_pdf_page)
    assert ratio >= 0.5
    assert classify_page(ratio, len(page_text(scan_pdf_page).strip())) == "scanned"

    text_pdf = pymupdf.open(stream=make(False, "hello world "*20), filetype="pdf")
    text_page = text_pdf.load_page(0)
    ratio_t = page_image_area_ratio(text_page)
    assert ratio_t < 0.5
    cls = classify_page(ratio_t, len(page_text(text_page).strip()))
    assert cls == "text"


def test_text_only_page_has_ratio_zero() -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((40, 40), "plain text page content")
    assert page_image_area_ratio(page) == 0.0