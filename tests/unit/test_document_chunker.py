"""
Unit tests for DocumentChunker (C4).

Uses a FakeSplitter so the tests don't need real langchain / external
services — everything is exercised against an in-memory chunk list.
"""

from __future__ import annotations

import pytest

from src.core.types import Chunk, Document, ImageRef
from src.ingestion.chunking import DocumentChunker
from src.libs.splitter.base_splitter import BaseSplitter


# ---------------------------------------------------------------------------
# FakeSplitter — a deterministic splitter for tests.
# ---------------------------------------------------------------------------

class FakeSplitter(BaseSplitter):
    """
    A splitter whose output is whatever the test gives it.

    Usage::

        FakeSplitter(["chunk A", "chunk B", "chunk C"])
    """

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = list(chunks)

    def split_text(self, text: str, **kwargs) -> list[str]:
        return list(self._chunks)


class EmptySplitter(BaseSplitter):
    """Returns an empty list — used to exercise the empty-input path."""

    def split_text(self, text: str, **kwargs) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def doc_with_text() -> Document:
    """A document with three text blocks (separated by double newlines)."""
    return Document(
        id="docabc123",  # production IDs are SHA256 hex (no underscores)
        text="First paragraph about topic A.\n\nSecond paragraph about B.\n\nThird paragraph about C.",
        metadata={
            "source_path": "/tmp/sample.pdf",
            "doc_type": "pdf",
            "page_count": 1,
        },
    )


@pytest.fixture
def doc_with_images() -> Document:
    """A document with embedded image refs and text that mentions some."""
    return Document(
        id="docimg001",
        text=(
            "Caption: see [IMAGE: img1] below.\n\n"
            "Another section [IMAGE: img2] [IMAGE: img3] here.\n\n"
            "Final section without any image."
        ),
        metadata={
            "source_path": "/tmp/pics.pdf",
            "doc_type": "pdf",
            "page_count": 2,
            "images": [
                ImageRef(id="img1", path="/data/images/docimg001/img1.png").to_dict(),
                ImageRef(id="img2", path="/data/images/docimg001/img2.png").to_dict(),
                ImageRef(id="img3", path="/data/images/docimg001/img3.png").to_dict(),
            ],
        },
    )


@pytest.fixture
def chunker() -> DocumentChunker:
    """Default chunker: one chunk per \\n\\n-separated block."""
    return DocumentChunker(FakeSplitter([
        "First paragraph about topic A.",
        "Second paragraph about B.",
        "Third paragraph about C.",
    ]))


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------

class TestBasicSplitting:
    def test_returns_list_of_chunks(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        assert isinstance(chunks, list)
        assert len(chunks) == 3
        assert all(isinstance(c, Chunk) for c in chunks)

    def test_chunks_preserve_text(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        assert [c.text for c in chunks] == [
            "First paragraph about topic A.",
            "Second paragraph about B.",
            "Third paragraph about C.",
        ]

    def test_empty_text_returns_empty_list(self):
        chunker = DocumentChunker(FakeSplitter([]))
        doc = Document(id="empty", text="", metadata={})
        assert chunker.split_document(doc) == []

    def test_splitter_returning_empty_yields_empty_list(self, doc_with_text):
        chunker = DocumentChunker(EmptySplitter())
        assert chunker.split_document(doc_with_text) == []

    def test_chunk_count_driven_by_splitter(self, doc_with_text):
        """Different splitters → different chunk counts (config-driven)."""
        chunker = DocumentChunker(FakeSplitter([
            "all-in-one",
        ]))
        chunks = chunker.split_document(doc_with_text)
        assert len(chunks) == 1
        assert chunks[0].text == "all-in-one"


# ---------------------------------------------------------------------------
# Chunk ID generation
# ---------------------------------------------------------------------------

class TestChunkId:
    def test_id_format(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        # {doc_id}_{index:04d}_{hash_8chars}
        for c in chunks:
            parts = c.id.split("_")
            assert len(parts) == 3
            assert parts[0] == "docabc123"
            assert parts[1].isdigit() and len(parts[1]) == 4
            assert len(parts[2]) == 8
            assert all(ch in "0123456789abcdef" for ch in parts[2])

    def test_id_includes_doc_id_prefix(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        for c in chunks:
            assert c.id.startswith("docabc123_")

    def test_ids_are_unique_within_document(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        ids = [c.id for c in chunks]
        assert len(ids) == len(set(ids))

    def test_ids_are_deterministic(self, chunker, doc_with_text):
        """Re-splitting the same document must yield identical IDs."""
        ids_a = [c.id for c in chunker.split_document(doc_with_text)]
        ids_b = [c.id for c in chunker.split_document(doc_with_text)]
        assert ids_a == ids_b

    def test_index_part_reflects_chunk_index(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        for i, c in enumerate(chunks):
            # The middle part is zero-padded to 4 digits
            assert c.id.split("_")[1] == f"{i:04d}"


# ---------------------------------------------------------------------------
# Metadata inheritance
# ---------------------------------------------------------------------------

class TestMetadataInheritance:
    def test_chunk_meta_copies_document_meta(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        for c in chunks:
            assert c.metadata["source_path"] == "/tmp/sample.pdf"
            assert c.metadata["doc_type"] == "pdf"
            assert c.metadata["page_count"] == 1

    def test_chunk_index_added(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        for i, c in enumerate(chunks):
            assert c.metadata["chunk_index"] == i

    def test_source_ref_points_to_parent_doc(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        for c in chunks:
            assert c.source_ref == "docabc123"

    def test_meta_isolated_from_document_meta_mutation(
        self, chunker, doc_with_text
    ):
        """Mutating a chunk's metadata must not affect the document."""
        chunks = chunker.split_document(doc_with_text)
        chunks[0].metadata["source_path"] = "/tmp/HACKED.pdf"
        assert doc_with_text.metadata["source_path"] == "/tmp/sample.pdf"

    def test_no_images_key_for_chunks_without_placeholders(
        self, chunker, doc_with_text
    ):
        chunks = chunker.split_document(doc_with_text)
        for c in chunks:
            assert "images" not in c.metadata
            assert "image_refs" not in c.metadata


# ---------------------------------------------------------------------------
# Image reference distribution
# ---------------------------------------------------------------------------

class TestImageDistribution:
    def test_chunk_with_placeholder_gets_matching_image(self, doc_with_images):
        chunker = DocumentChunker(FakeSplitter([
            "Caption: see [IMAGE: img1] below.",
            "Another section [IMAGE: img2] [IMAGE: img3] here.",
            "Final section without any image.",
        ]))
        chunks = chunker.split_document(doc_with_images)
        # Chunk 0 mentions img1
        c0 = chunks[0]
        assert c0.metadata["image_refs"] == ["img1"]
        assert len(c0.metadata["images"]) == 1
        assert c0.metadata["images"][0]["id"] == "img1"

    def test_chunk_with_multiple_placeholders_gets_all(self, doc_with_images):
        chunker = DocumentChunker(FakeSplitter([
            "Caption: see [IMAGE: img1] below.",
            "Another section [IMAGE: img2] [IMAGE: img3] here.",
            "Final section without any image.",
        ]))
        chunks = chunker.split_document(doc_with_images)
        c1 = chunks[1]
        assert c1.metadata["image_refs"] == ["img2", "img3"]
        assert len(c1.metadata["images"]) == 2
        ids = {img["id"] for img in c1.metadata["images"]}
        assert ids == {"img2", "img3"}

    def test_chunk_without_placeholder_has_no_images_key(self, doc_with_images):
        chunker = DocumentChunker(FakeSplitter([
            "Caption: see [IMAGE: img1] below.",
            "Another section [IMAGE: img2] [IMAGE: img3] here.",
            "Final section without any image.",
        ]))
        chunks = chunker.split_document(doc_with_images)
        c2 = chunks[2]
        assert "images" not in c2.metadata
        assert "image_refs" not in c2.metadata

    def test_unknown_image_id_in_placeholder_silently_dropped(self):
        """Placeholder for an image not in document.images is ignored."""
        doc = Document(
            id="docorphan",
            text="See [IMAGE: known] and [IMAGE: ghost] here.",
            metadata={
                "source_path": "/tmp/x.pdf",
                "images": [
                    ImageRef(id="known", path="/data/known.png").to_dict(),
                ],
            },
        )
        chunker = DocumentChunker(FakeSplitter([
            "See [IMAGE: known] and [IMAGE: ghost] here.",
        ]))
        chunks = chunker.split_document(doc)
        assert chunks[0].metadata["image_refs"] == ["known"]
        assert len(chunks[0].metadata["images"]) == 1

    def test_document_with_no_images_yields_no_image_keys(self, doc_with_text):
        """A document without images → no chunk gets an 'images' key."""
        chunker = DocumentChunker(FakeSplitter([
            "A", "B", "C",
        ]))
        chunks = chunker.split_document(doc_with_text)
        for c in chunks:
            assert "images" not in c.metadata
            assert "image_refs" not in c.metadata

    def test_image_dict_is_deep_copy_not_shared_reference(
        self, doc_with_images
    ):
        """Mutating a chunk's images must not affect the parent doc."""
        chunker = DocumentChunker(FakeSplitter([
            "Caption: see [IMAGE: img1] below.",
        ]))
        chunks = chunker.split_document(doc_with_images)
        chunks[0].metadata["images"][0]["id"] = "HACKED"
        # Reload from doc.metadata to be sure
        assert doc_with_images.metadata["images"][0]["id"] == "img1"

    def test_image_refs_preserves_appearance_order(self, doc_with_images):
        """image_refs order should follow placeholder order in text,
        not document.images order."""
        doc = Document(
            id="docorder",
            # Note: img3 appears before img2 in text
            text="[IMAGE: img3] then [IMAGE: img1] then [IMAGE: img2].",
            metadata={
                "source_path": "/tmp/x.pdf",
                "images": [
                    ImageRef(id="img1", path="/data/img1.png").to_dict(),
                    ImageRef(id="img2", path="/data/img2.png").to_dict(),
                    ImageRef(id="img3", path="/data/img3.png").to_dict(),
                ],
            },
        )
        chunker = DocumentChunker(FakeSplitter([
            "[IMAGE: img3] then [IMAGE: img1] then [IMAGE: img2].",
        ]))
        chunks = chunker.split_document(doc)
        assert chunks[0].metadata["image_refs"] == ["img3", "img1", "img2"]

    def test_decorative_images_excluded_from_chunk(self):
        """ImageRef with is_content=False is not attached to chunks."""
        doc = Document(
            id="docdeco",
            text="See [IMAGE: logo] and [IMAGE: diagram] here.",
            metadata={
                "source_path": "/tmp/x.pdf",
                "images": [
                    ImageRef(
                        id="logo",
                        path="/data/logo.png",
                        is_content=False,
                        classification_reason="size_filter",
                    ).to_dict(),
                    ImageRef(
                        id="diagram",
                        path="/data/diagram.png",
                        is_content=True,
                    ).to_dict(),
                ],
            },
        )
        chunker = DocumentChunker(FakeSplitter([
            "See [IMAGE: logo] and [IMAGE: diagram] here.",
        ]))
        chunks = chunker.split_document(doc)
        assert chunks[0].metadata["image_refs"] == ["diagram"]
        assert len(chunks[0].metadata["images"]) == 1
        assert chunks[0].metadata["images"][0]["id"] == "diagram"


# ---------------------------------------------------------------------------
# Offsets
# ---------------------------------------------------------------------------

class TestOffsets:
    def test_offsets_within_full_text(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        for c in chunks:
            assert 0 <= c.start_offset < c.end_offset
            assert c.end_offset <= len(doc_with_text.text)
            # The chunk text appears at the recorded offset
            assert doc_with_text.text[c.start_offset:c.end_offset] == c.text


# ---------------------------------------------------------------------------
# Type contract
# ---------------------------------------------------------------------------

class TestTypeContract:
    def test_output_is_chunk_dataclass(self, chunker, doc_with_text):
        chunks = chunker.split_document(doc_with_text)
        for c in chunks:
            # Round-trip through the dataclass serializer
            d = c.to_dict()
            assert d["id"] == c.id
            assert d["text"] == c.text
            assert d["start_offset"] == c.start_offset
            assert d["end_offset"] == c.end_offset
            assert d["source_ref"] == c.source_ref
