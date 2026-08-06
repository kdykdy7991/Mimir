"""
Contract tests for ``MarkdownLoader`` (M5).

Mirrors ``test_loader_pdf_contract.py`` for the Markdown path. Covers:

- structure preservation: headings / lists / tables / blockquotes /
  fenced code blocks survive the round-trip verbatim;
- encoding: UTF-8 (with BOM) and CRLF line endings are normalised;
- rejection: binary files, undecodable bytes, empty documents and
  missing files raise ``LoaderError`` (nothing is indexed);
- metadata: ``source_path`` / ``doc_type`` / ``title`` /
  ``heading_outline`` / ``doc_hash`` honour the C3 contract;
- outline extraction skips fenced code blocks and non-heading ``#foo``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.types import Document
from src.libs.loader import LoaderError, MarkdownLoader

FENCE = "```"


def _write(path: Path, text: str, *, mode: str = "w", **kwargs) -> Path:
    if mode == "w":
        path.write_text(text, encoding="utf-8", **kwargs)
    else:
        path.write_bytes(text)  # type: ignore[arg-type]
    return path


@pytest.fixture
def md_path(tmp_path) -> Path:
    return tmp_path / "guide.md"


@pytest.fixture
def loader() -> MarkdownLoader:
    return MarkdownLoader()


# ---------------------------------------------------------------------------
# Structure preservation
# ---------------------------------------------------------------------------

class TestStructurePreserved:
    @pytest.mark.parametrize("ext", [".md", ".markdown"])
    def test_all_block_types_survive_verbatim(self, tmp_path, loader, ext: str) -> None:
        text = "\n".join([
            "# Title",
            "",
            "## Section",
            "",
            "- item one",
            "- item two",
            "",
            "> a blockquote",
            "",
            "| A | B |",
            "|---|---|",
            "| 1 | 2 |",
            "",
            FENCE + "python",
            "def hello():",
            "    return 'hi'",
            FENCE,
            "",
            "trailing paragraph",
            "",
        ])
        p = _write(tmp_path / f"doc{ext}", text)
        doc = loader.load(str(p))

        assert doc.text.count("# Title") == 1
        assert "- item one" in doc.text
        assert "| A | B |" in doc.text and "| 1 | 2 |" in doc.text
        assert "> a blockquote" in doc.text
        # Fenced block kept intact including the info string and body.
        assert FENCE + "python" in doc.text
        assert "    return 'hi'" in doc.text
        assert "trailing paragraph" in doc.text

    def test_crlf_and_bom_are_normalised(self, tmp_path, loader) -> None:
        p = _write(
            tmp_path / "bom.md",
            b"\xef\xbb\xbf# Title\r\n\r\nbody\r\n",
            mode="wb",
        )
        doc = loader.load(str(p))
        assert doc.text == "# Title\n\nbody\n"
        assert doc.metadata["title"] == "Title"

    def test_whitespace_and_blank_runs_preserved(self, tmp_path, loader) -> None:
        """Trailing whitespace and blank-line runs are content — preserved."""
        p = _write(tmp_path / "ws.md", "line1   \n\n\n\n\nline2\n")
        doc = loader.load(str(p))
        assert doc.text == "line1   \n\n\n\n\nline2\n"

    def test_hard_line_break_trailing_spaces_preserved(self, tmp_path, loader) -> None:
        """Two trailing spaces = Markdown hard line break; must survive."""
        p = _write(tmp_path / "hard.md", "line one  \nline two\n")
        doc = loader.load(str(p))
        assert doc.text == "line one  \nline two\n"

    def test_code_block_trailing_whitespace_preserved(self, tmp_path, loader) -> None:
        """Trailing spaces inside a fenced code block are code content."""
        text = FENCE + "\nfor x in items:  \n    pass\n" + FENCE + "\n"
        p = _write(tmp_path / "code_ws.md", text)
        doc = loader.load(str(p))
        assert "for x in items:  \n" in doc.text

    def test_code_block_blank_lines_preserved(self, tmp_path, loader) -> None:
        """Consecutive blank lines inside a code block are not collapsed."""
        text = FENCE + "\ndef f():\n\n\n\n    return 1\n" + FENCE + "\n"
        p = _write(tmp_path / "code_blank.md", text)
        doc = loader.load(str(p))
        assert "def f():\n\n\n\n    return 1\n" in doc.text


# ---------------------------------------------------------------------------
# Rejection paths
# ---------------------------------------------------------------------------

class TestRejection:
    def test_binary_file_raises_loader_error(self, tmp_path, loader) -> None:
        p = _write(tmp_path / "fake.md", b"%PDF-1.4\x00\x00fake", mode="wb")
        with pytest.raises(LoaderError, match="binary"):
            loader.load(str(p))

    def test_undecodable_bytes_raise_loader_error(self, tmp_path, loader) -> None:
        p = _write(tmp_path / "bad.md", b"\xff\xfe\x80 broken utf8", mode="wb")
        with pytest.raises(LoaderError, match="decode"):
            loader.load(str(p))

    def test_empty_document_raises_loader_error(self, tmp_path, loader) -> None:
        p = _write(tmp_path / "empty.md", "   \n\n  \n")
        with pytest.raises(LoaderError, match="empty"):
            loader.load(str(p))

    def test_missing_file_raises_loader_error(self, tmp_path, loader) -> None:
        with pytest.raises(LoaderError, match="not found"):
            loader.load(str(tmp_path / "nope.md"))

    def test_directory_raises_loader_error(self, tmp_path, loader) -> None:
        with pytest.raises(LoaderError, match="not a regular file"):
            loader.load(str(tmp_path))


# ---------------------------------------------------------------------------
# Metadata contract
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_metadata_contract(self, tmp_path, loader) -> None:
        p = _write(tmp_path / "meta.md", "# Project\n\nIntro.\n")
        doc = loader.load(str(p))
        assert doc.metadata["source_path"] == str(p)
        assert doc.metadata["doc_type"] == "markdown"
        assert doc.metadata["title"] == "Project"
        assert doc.metadata["heading_outline"] == [{"level": 1, "text": "Project"}]
        assert len(doc.metadata["doc_hash"]) == 64  # full sha256 hex
        # No pagination / images keys for Markdown.
        assert "page" not in doc.metadata
        assert "images" not in doc.metadata

    def test_document_id_is_stable_16_hex_of_content(self, tmp_path, loader) -> None:
        p = _write(tmp_path / "id.md", "# Title\n\nbody\n")
        first = loader.load(str(p)).id
        second = loader.load(str(p)).id
        assert first == second
        assert len(first) == 16
        # Id is content-addressed: a different body yields a different id.
        other = _write(tmp_path / "id2.md", "# Title\n\nother body\n")
        assert loader.load(str(other)).id != first

    def test_title_falls_back_to_filename_stem(self, tmp_path, loader) -> None:
        p = _write(tmp_path / "no_heading.md", "plain prose without a title\n")
        doc = loader.load(str(p))
        assert doc.metadata["title"] == "no_heading"
        assert doc.metadata["heading_outline"] == []


# ---------------------------------------------------------------------------
# Heading outline extraction
# ---------------------------------------------------------------------------

class TestHeadingOutline:
    def test_outline_collects_levels_in_order(self, tmp_path, loader) -> None:
        text = "\n".join([
            "# One",
            "## Two",
            "### Three",
            "#### Four",
            "##### Five",
            "###### Six",
            "## Seven",
        ])
        p = _write(tmp_path / "outline.md", text)
        outline = loader.load(str(p)).metadata["heading_outline"]
        assert [o["level"] for o in outline] == [1, 2, 3, 4, 5, 6, 2]
        assert [o["text"] for o in outline] == [
            "One", "Two", "Three", "Four", "Five", "Six", "Seven",
        ]

    def test_outline_skips_fenced_code_blocks(self, tmp_path, loader) -> None:
        text = "\n".join([
            "# Real",
            FENCE + "md",
            "# Fake heading in code",
            FENCE,
            "## Real Two",
        ])
        p = _write(tmp_path / "fenced.md", text)
        outline = loader.load(str(p)).metadata["heading_outline"]
        assert [o["text"] for o in outline] == ["Real", "Real Two"]

    def test_outline_excludes_inline_hashes_and_indented_code(self, tmp_path, loader) -> None:
        text = "# Real\n\n#notheading\n\n    # indented code\n\n## Fine\n"
        p = _write(tmp_path / "edge.md", text)
        outline = loader.load(str(p)).metadata["heading_outline"]
        assert [o["text"] for o in outline] == ["Real", "Fine"]


# ---------------------------------------------------------------------------
# BaseLoader conveniences
# ---------------------------------------------------------------------------

class TestLoadMany:
    def test_load_many_preserves_order(self, tmp_path, loader) -> None:
        a = _write(tmp_path / "a.md", "# A\n")
        b = _write(tmp_path / "b.markdown", "# B\n")
        docs = loader.load_many([str(a), str(b)])
        assert [d.metadata["title"] for d in docs] == ["A", "B"]
        assert all(isinstance(d, Document) for d in docs)
