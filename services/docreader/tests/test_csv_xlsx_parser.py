# SPDX-License-Identifier: MIT
# Phase 6 (XLSX/CSV #2): parser + acceptance tests. Samples are generated
# in-memory with the stdlib (csv / zipfile + ElementTree) — no openpyxl.
"""Tests for CSV and XLSX -> GFM Markdown parsers."""

from __future__ import annotations

import io
import zipfile

from docreader.parser.csv_parser import CsvParser
from docreader.parser.registry import list_engines, parse_file
from docreader.parser.xlsx_parser import XlsxParser

_W = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def test_csv_to_markdown() -> None:
    raw = b"name,qty\npen,3\nink,5\n"
    doc = CsvParser().parse(raw)
    assert "| name | qty |" in doc.content
    assert "| pen | 3 |" in doc.content
    assert "| ink | 5 |" in doc.content
    assert "| --- | --- |" in doc.content


def test_csv_pipe_escaped() -> None:
    doc = CsvParser().parse(b"a|b\nx\n")
    assert "a\\|b" in doc.content


def _xlsx_bytes() -> bytes:
    shared = ("xl/sharedStrings.xml", (
        '<?xml version="1.0"?><sst xmlns="' + _W[1:-1] + '">'
        '<si><t>name</t></si><si><t>qty</t></si><si><t>pen</t></si></sst>'
    ).encode())
    sheet = ("xl/worksheets/sheet1.xml", (
        '<?xml version="1.0"?><worksheet xmlns="' + _W[1:-1] + '">'
        '<sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>3</v></c></row>'
        '<row r="3"><c r="A3"><v>7</v></c><c r="B3" t="inlineStr"><is><t>z</t></is></c></row>'
        '</sheetData></worksheet>'
    ).encode())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", b"<x/>")
        zf.writestr(shared[0], shared[1])
        zf.writestr(sheet[0], sheet[1])
    return buf.getvalue()


def test_xlsx_to_markdown_shared_inline_numeric() -> None:
    doc = XlsxParser().parse(_xlsx_bytes())
    assert "| name | qty |" in doc.content
    assert "| pen | 3 |" in doc.content
    assert "| 7 | z |" in doc.content


def test_xlsx_invalid_rejected() -> None:
    import pytest
    with pytest.raises(ValueError):
        XlsxParser().parse(b"not a zip")


def test_named_sheets_order_merges_formula_bool() -> None:
    _R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    _PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    wb = (
        f'<?xml version="1.0"?><workbook xmlns="{_W[1:-1]}" '
        f'xmlns:r="{_R[1:-1]}"><sheets>'
        '<sheet name="Alpha" sheetId="1" r:id="rId1"/>'
        '<sheet name="Zeta" sheetId="2" r:id="rId2"/>'
        '</sheets></workbook>'
    ).encode()
    wb_rels = (
        f'<?xml version="1.0"?><Relationships xmlns="{_PKG[1:-1]}">'
        '<Relationship Id="rId1" Type="x/worksheet" Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId2" Type="x/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    ).encode()
    # sheet2 is displayed first ("Alpha"); also has a merge, a boolean and a
    # formula whose cached <v> holds the result.
    alpha_sheet = (
        f'<?xml version="1.0"?><worksheet xmlns="{_W[1:-1]}">'
        '<mergeCells><mergeCell ref="A1:B2"/></mergeCells>'
        '<sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>merged-title</t></is></c></row>'
        '<row r="3"><c r="C3"><f>SUM(1,2,3)</f><v>6</v></c></row>'
        '<row r="4"><c r="A4" t="b"><v>1</v></c></row>'
        '</sheetData></worksheet>'
    ).encode()
    zeta_sheet = (
        f'<?xml version="1.0"?><worksheet xmlns="{_W[1:-1]}">'
        '<sheetData><row r="1"><c r="A1"><v>99</v></c></row></sheetData></worksheet>'
    ).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", zeta_sheet)
        zf.writestr("xl/worksheets/sheet2.xml", alpha_sheet)
    doc = XlsxParser().parse(buf.getvalue())
    assert doc.content.index("## Alpha") < doc.content.index("## Zeta")
    # merged title repeated into covered B1/A2/B2
    assert "merged-title" in doc.content
    # formula cached value
    assert "6" in doc.content
    # boolean
    assert "TRUE" in doc.content
    # second sheet value present
    assert "99" in doc.content


def test_csv_and_xlsx_registered_and_routed() -> None:
    engines = list_engines()
    builtin = next(e for e in engines if e["name"] == "builtin")
    assert {"csv", "xlsx"}.issubset(set(builtin["file_types"]))
    csv_doc = parse_file("t.csv", "csv", b"a,b\n1,2\n")
    assert "| a | b |" in csv_doc.content
    xl_doc = parse_file("t.xlsx", "xlsx", _xlsx_bytes())
    assert "| name | qty |" in xl_doc.content