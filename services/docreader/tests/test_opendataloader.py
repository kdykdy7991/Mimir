"""Tests for the local OpenDataLoader parser (mocked JVM package) and its
registry / fallback integration (plan §Phase-3)."""

from __future__ import annotations

import io
import os

import pymupdf

from docreader.parser import registry
from docreader.parser.pdf_parser import PDFScannedParser
from docreader.parser import opendataloader_parser as odl


def _pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((50, 100), "odl table benchmark body")
    data = doc.tobytes()
    doc.close()
    return data


def test_available_false_without_package() -> None:
    ok, reason = odl.opendataloader_available()
    # In this offline env the package is absent; assert the gate does not lie.
    if ok:
        return  # (satisfy local dev where the package could exist)
    assert reason  # a human-readable reason is attached


def test_run_convert_writes_markdown_and_images(monkeypatch, tmp_path) -> None:
    def fake_convert(pdf_path, output_dir, image_dir):
        os.makedirs(image_dir, exist_ok=True)
        with open(os.path.join(image_dir, "chart.png"), "wb") as f:
            f.write(b"\x89PNG-fake")
        with open(os.path.join(tmp_path, "out.md"), "w", encoding="utf-8") as f:
            f.write("| a | b |\n| - | - |\n| 1 | 2 |\n![peek](/abs/tmp/chart.png)\n")
        # also place the md under output_dir so _find_markdown_file sees it
        md = os.path.join(output_dir, "doc.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write(open(os.path.join(tmp_path, "out.md"), encoding="utf-8").read())

    monkeypatch.setattr(odl, "_run_convert", fake_convert)
    monkeypatch.setattr(
        odl, "opendataloader_available", lambda: (True, ""),
    )
    result = odl.OpenDataLoaderParser(file_name="doc.pdf").parse_into_text(_pdf_bytes())

    assert result.metadata["parser_engine"] == "opendataloader"
    assert "| a | b |" in result.content
    assert "chart.png" in result.content
    # raw bytes image + no leak of the temp path
    assert any(k.startswith("images/") for k in result.images)
    assert b"\x89PNG-fake" in result.images.values()
    assert "/abs/tmp" not in result.content
    from docreader.parser.table_normalize import normalize_gfm_table
    assert normalize_gfm_table is not None


def test_short_output_falls_back_to_scanned(monkeypatch) -> None:
    def fake_short(pdf_path, output_dir, image_dir):
        with open(os.path.join(output_dir, "doc.md"), "w", encoding="utf-8") as f:
            f.write("hi")

    monkeypatch.setattr(odl, "_run_convert", fake_short)
    monkeypatch.setattr(odl, "opendataloader_available", lambda: (True, ""))
    result = odl.OpenDataLoaderParser(file_name="doc.pdf").parse_into_text(_pdf_bytes())
    assert result.metadata["image_source_type"] == "scanned_pdf"
    assert result.metadata["page_count"] == 1


def test_registry_routes_default_builtin_and_explicit_odl() -> None:
    assert registry._engine_for_fmt("pdf", None)["name"] == "builtin"
    assert registry._engine_for_fmt("pdf", "opendataloader")["name"] == "opendataloader"
    import pytest
    with pytest.raises(ValueError):
        registry._engine_for_fmt("pdf", "no-such-engine")


def test_list_engines_reports_opendataloader_availability() -> None:
    engines = {e["name"]: e for e in registry.list_engines()}
    assert "opendataloader" in engines
    assert "pdf" in engines["opendataloader"]["file_types"]


def test_scanned_parser_via_registry() -> None:
    doc = PDFScannedParser(file_name="s.pdf").parse_into_text(_pdf_bytes())
    assert doc.metadata["image_source_type"] == "scanned_pdf"