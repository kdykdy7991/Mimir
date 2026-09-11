"""
B1.3 — normalized ``source_locator`` mapping + degradation.

Covers every file-format family the task book requires and the "never
guess" rule: PDF → 1-based page, image → whole image, DOCX/Markdown/TXT
→ ``section`` when a heading exists, otherwise ``none``.
"""

from __future__ import annotations

import pytest

from src.ingestion.chunk_order import (
    build_source_locator,
    chunk_id_of,
    chunk_sort_key,
    heading_of,
    page_number_of,
    stable_order_chunks,
)


def loc(metadata: dict | None = None, **kw) -> dict:
    return build_source_locator(metadata or {}, **kw)


def test_pdf_returns_one_based_page() -> None:
    assert loc({"page": 12}, source_path="doc.pdf") == {"kind": "pdf_page", "page": 12}
    assert loc({"page_num": 1}, source_path="doc.pdf") == {"kind": "pdf_page", "page": 1}


def test_pdf_without_page_degrades_to_none() -> None:
    assert loc({}, source_path="doc.pdf") == {"kind": "none", "page": None}


def test_pdf_with_zero_or_negative_page_degrades() -> None:
    assert loc({"page": 0}, source_path="doc.pdf") == {"kind": "none", "page": None}
    assert loc({"page": -3}, source_path="doc.pdf") == {"kind": "none", "page": None}


def test_image_extension_returns_whole_image() -> None:
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        assert loc({"page": 3}, source_path=f"photo{ext}") == {"kind": "image", "page": None}


def test_image_content_type_returns_image_even_without_extension() -> None:
    assert loc(
        {"content_type": "image_ocr", "page": 5},
        content_type="image_ocr",
    ) == {"kind": "image", "page": None}
    assert loc(
        {"content_type": "image_caption"},
        content_type="image_caption",
    ) == {"kind": "image", "page": None}


def test_section_with_metadata_heading() -> None:
    assert loc(
        {"heading": "3.2 服务部署"}, source_path="guide.docx",
    ) == {"kind": "section", "page": None}
    assert loc(
        {"section": "简介"}, source_path="notes.txt",
    ) == {"kind": "section", "page": None}


def test_section_via_text_heading_parameter() -> None:
    assert loc(
        {}, source_path="readme.md", heading="安装",
    ) == {"kind": "section", "page": None}


def test_section_ext_without_heading_degrades_to_none() -> None:
    assert loc({}, source_path="readme.md") == {"kind": "none", "page": None}
    assert loc({}, source_path="notes.txt") == {"kind": "none", "page": None}
    assert loc({"page": 2}, source_path="plain.docx") == {"kind": "none", "page": None}


def test_unknown_extension_is_none() -> None:
    assert loc({"page": 1}, source_path="data.rst") == {"kind": "none", "page": None}
    assert loc({}, source_path=None) == {"kind": "none", "page": None}


def test_no_reliable_locator_is_never_guessed() -> None:
    """Without a recognizable family we return ``none``, not a guess."""
    assert loc({"page": 3}, source_path="unknown.bin")["kind"] == "none"


# --- ordering / heading helpers (shared single source) ----------------------

def test_page_number_normalization() -> None:
    assert page_number_of({"page": 12}) == 12
    assert page_number_of({"page_num": "4"}) == 4
    assert page_number_of({"page": "abc"}) is None
    assert page_number_of({}) is None


def test_heading_derivation() -> None:
    hit = {"text": "## 目标\n正文", "metadata": {}}
    assert heading_of(hit) == "目标"
    assert heading_of({"text": "no heading", "metadata": {"section": "附录"}}) == "附录"


def test_chunk_id_fallback_and_sort_key() -> None:
    assert chunk_id_of({"id": "x", "metadata": {}}) == "x"
    assert chunk_id_of({"id": "", "metadata": {"chunk_id": "y"}}) == "y"

    assert chunk_sort_key({"id": "a", "metadata": {"chunk_index": 1}})[0:2] == (0, 1)
    assert chunk_sort_key({"id": "doc_0003_c"})[0:2] == (1, 3)
    assert chunk_sort_key({"id": "z"})[0] == 2


def test_stable_order_chunks_is_deterministic() -> None:
    hits = [
        {"id": "b", "text": "", "metadata": {"chunk_index": 2}},
        {"id": "doc_0001_x", "text": "", "metadata": {}},
        {"id": "a", "text": "", "metadata": {"chunk_index": 1}},
    ]
    ordered = stable_order_chunks(hits)
    # (0,1) a, (0,2) b, (1,1) legacy → chunk_index-ranked rows come first.
    assert [chunk_id_of(h) for h in ordered] == ["a", "b", "doc_0001_x"]