"""Tests for table / markdown normalization (plan Phase-3 items 3-5)."""

from __future__ import annotations

from docreader.parser.table_normalize import (
    normalize_gfm_table,
    normalize_html_table,
    normalize_markdown,
    rewrite_abs_image_paths,
)


def test_gfm_table_separator_normalized() -> None:
    block = (
        "| Name | Amount |\n"
        "| - | - : |\n"
        "| Pens | 10 |\n"
    )
    out = normalize_gfm_table(block)
    sepline = out.splitlines()[1]
    assert sepline == "|---|---:|"  # left default, right-aligned
    assert "| Name" in out and "| Pens" in out


def test_clean_table_kept_as_cleaned_html() -> None:
    block = (
        '<table border="1" style="color:red">'
        "<tr><th>a</th><th>b</th></tr>"
        "<tr><td>1</td><td>2</td></tr></table>"
    )
    out = normalize_html_table(block)
    assert "style" not in out and 'border="1"' not in out
    assert "<table>" in out
    assert "<th>a</th>" in out or "<th>a</th>" in out


def test_rowspan_table_keeps_html_and_meaning() -> None:
    block = (
        '<table><tr><td rowspan="2">A</td><td>B</td></tr>'
        '<tr><td>C</td></tr></table>'
    )
    out = normalize_html_table(block)
    assert "rowspan" in out  # structural meaning preserved
    assert "style" not in out and "class" not in out


def test_abs_image_path_rewritten() -> None:
    text = "before\n![Fig](/tmp/weknora-odl-abc123/images/chart.png)\n![ok](images/ref.png)\n"
    out = rewrite_abs_image_paths(text)
    assert "/tmp/weknora-odl-abc123" not in out
    assert "images/chart.png" in out
    assert "images/ref.png" in out  # already-relative untouched
    assert "data:image/png;base64," not in out


def test_normalize_markdown_no_leak_and_keeps_body() -> None:
    text = "body\n![x](/abs/path/f.png)\nbody2"
    out = normalize_markdown(text)
    assert "/abs/path" not in out
    assert "images/f.png" in out
    assert "body" in out and "body2" in out
    # no path / base64 leak
    assert "TemporaryDirectory" not in out