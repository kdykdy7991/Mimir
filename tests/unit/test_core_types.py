"""
Unit tests for core data types/contracts.

Tests cover:
- ImageRef
- Document
- Chunk
- ChunkRecord
- ProcessedQuery
- RetrievalResult
"""

from __future__ import annotations

import pytest

from src.core.types import (
    ImageRef,
    Document,
    Chunk,
    ChunkRecord,
    ProcessedQuery,
    RetrievalResult,
)


# ---------------------------------------------------------------------------
# Tests: ImageRef
# ---------------------------------------------------------------------------

class TestImageRef:
    """Test ImageRef dataclass."""

    def test_create_image_ref(self):
        """Create ImageRef with required fields."""
        ref = ImageRef(id="img_001", path="data/images/doc1.png")
        assert ref.id == "img_001"
        assert ref.path == "data/images/doc1.png"
        assert ref.page is None
        assert ref.text_offset == 0

    def test_create_image_ref_with_all_fields(self):
        """Create ImageRef with all fields."""
        ref = ImageRef(
            id="img_001",
            path="data/images/doc1.png",
            page=5,
            text_offset=100,
            text_length=20,
            position={"x": 10, "y": 20, "width": 100, "height": 50},
        )
        assert ref.page == 5
        assert ref.text_offset == 100
        assert ref.position["x"] == 10

    def test_to_dict(self):
        """Convert to dictionary."""
        ref = ImageRef(id="img_001", path="doc.png", page=1)
        d = ref.to_dict()
        assert d["id"] == "img_001"
        assert d["path"] == "doc.png"
        assert d["page"] == 1

    def test_from_dict(self):
        """Create from dictionary."""
        d = {"id": "img_001", "path": "doc.png", "page": 1}
        ref = ImageRef.from_dict(d)
        assert ref.id == "img_001"
        assert ref.path == "doc.png"


# ---------------------------------------------------------------------------
# Tests: Document
# ---------------------------------------------------------------------------

class TestDocument:
    """Test Document dataclass."""

    def test_create_document(self):
        """Create Document with required fields."""
        doc = Document(id="doc_001", text="Hello world")
        assert doc.id == "doc_001"
        assert doc.text == "Hello world"
        assert doc.metadata == {}

    def test_create_document_with_metadata(self):
        """Create Document with metadata."""
        doc = Document(
            id="doc_001",
            text="Hello",
            metadata={"source_path": "/path/to/file.pdf", "page": 1},
        )
        assert doc.source_path == "/path/to/file.pdf"

    def test_to_dict(self):
        """Convert to dictionary."""
        doc = Document(id="doc_001", text="Hello", metadata={"key": "value"})
        d = doc.to_dict()
        assert d["id"] == "doc_001"
        assert d["text"] == "Hello"
        assert d["metadata"]["key"] == "value"

    def test_from_dict(self):
        """Create from dictionary."""
        d = {"id": "doc_001", "text": "Hello", "metadata": {}}
        doc = Document.from_dict(d)
        assert doc.id == "doc_001"

    def test_serialization_roundtrip(self):
        """Serialize and deserialize preserves data."""
        doc = Document(
            id="doc_001",
            text="Hello world",
            metadata={"source_path": "test.pdf", "images": [
                {"id": "img_001", "path": "img.png", "page": 1}
            ]},
        )
        d = doc.to_dict()
        doc2 = Document.from_dict(d)
        assert doc2.id == doc.id
        assert doc2.text == doc.text
        assert len(doc2.images) == 1
        assert doc2.images[0].id == "img_001"


# ---------------------------------------------------------------------------
# Tests: Chunk
# ---------------------------------------------------------------------------

class TestChunk:
    """Test Chunk dataclass."""

    def test_create_chunk(self):
        """Create Chunk with required fields."""
        chunk = Chunk(id="chunk_001", text="Hello world")
        assert chunk.id == "chunk_001"
        assert chunk.text == "Hello world"
        assert chunk.start_offset == 0
        assert chunk.end_offset == 0

    def test_create_chunk_with_position(self):
        """Create Chunk with position info."""
        chunk = Chunk(
            id="chunk_001",
            text="Hello",
            start_offset=10,
            end_offset=15,
            source_ref="doc_001",
        )
        assert chunk.start_offset == 10
        assert chunk.end_offset == 15
        assert chunk.source_ref == "doc_001"

    def test_to_dict(self):
        """Convert to dictionary."""
        chunk = Chunk(id="chunk_001", text="Hello", start_offset=10, end_offset=15)
        d = chunk.to_dict()
        assert d["id"] == "chunk_001"
        assert d["start_offset"] == 10
        assert d["end_offset"] == 15

    def test_from_dict(self):
        """Create from dictionary."""
        d = {"id": "chunk_001", "text": "Hello", "start_offset": 0, "end_offset": 5}
        chunk = Chunk.from_dict(d)
        assert chunk.id == "chunk_001"

    def test_serialization_roundtrip(self):
        """Serialize and deserialize preserves data."""
        chunk = Chunk(
            id="chunk_001",
            text="Hello world",
            metadata={"source_path": "test.pdf"},
            start_offset=0,
            end_offset=11,
            source_ref="doc_001",
        )
        d = chunk.to_dict()
        chunk2 = Chunk.from_dict(d)
        assert chunk2.id == chunk.id
        assert chunk2.start_offset == chunk.start_offset
        assert chunk2.source_ref == chunk.source_ref


# ---------------------------------------------------------------------------
# Tests: ChunkRecord
# ---------------------------------------------------------------------------

class TestChunkRecord:
    """Test ChunkRecord dataclass."""

    def test_create_chunk_record(self):
        """Create ChunkRecord with required fields."""
        record = ChunkRecord(id="record_001", text="Hello")
        assert record.id == "record_001"
        assert record.dense_vector is None
        assert record.sparse_vector is None

    def test_create_from_chunk(self):
        """Create ChunkRecord from Chunk."""
        chunk = Chunk(id="chunk_001", text="Hello", source_ref="doc_001")
        record = ChunkRecord.from_chunk(
            chunk,
            dense_vector=[0.1, 0.2, 0.3],
            sparse_vector={"hello": 1.0, "world": 0.5},
        )
        assert record.id == "chunk_001"
        assert record.source_ref == "doc_001"
        assert record.dense_vector == [0.1, 0.2, 0.3]
        assert record.sparse_vector["hello"] == 1.0

    def test_to_dict(self):
        """Convert to dictionary."""
        record = ChunkRecord(
            id="record_001",
            text="Hello",
            dense_vector=[0.1, 0.2],
        )
        d = record.to_dict()
        assert d["dense_vector"] == [0.1, 0.2]

    def test_from_dict(self):
        """Create from dictionary."""
        d = {
            "id": "record_001",
            "text": "Hello",
            "metadata": {},
            "start_offset": 0,
            "end_offset": 5,
            "dense_vector": [0.1, 0.2],
            "sparse_vector": {"hello": 1.0},
        }
        record = ChunkRecord.from_dict(d)
        assert record.id == "record_001"
        assert record.dense_vector == [0.1, 0.2]


# ---------------------------------------------------------------------------
# Tests: ProcessedQuery
# ---------------------------------------------------------------------------

class TestProcessedQuery:
    """Test ProcessedQuery dataclass."""

    def test_create_processed_query(self):
        """Create ProcessedQuery with required fields."""
        query = ProcessedQuery(original="What is RAG?")
        assert query.original == "What is RAG?"
        assert query.keywords == []

    def test_create_with_keywords(self):
        """Create with keywords and filters."""
        query = ProcessedQuery(
            original="What is RAG?",
            keywords=["RAG", "retrieval"],
            filters={"collection": "docs"},
        )
        assert len(query.keywords) == 2
        assert query.filters["collection"] == "docs"

    def test_to_dict(self):
        """Convert to dictionary."""
        query = ProcessedQuery(original="test", keywords=["test"])
        d = query.to_dict()
        assert d["original"] == "test"
        assert d["keywords"] == ["test"]


# ---------------------------------------------------------------------------
# Tests: RetrievalResult
# ---------------------------------------------------------------------------

class TestRetrievalResult:
    """Test RetrievalResult dataclass."""

    def test_create_retrieval_result(self):
        """Create RetrievalResult with required fields."""
        chunk = ChunkRecord(id="chunk_001", text="Hello")
        result = RetrievalResult(chunk=chunk, score=0.95)
        assert result.score == 0.95
        assert result.rank == 0

    def test_create_with_rank(self):
        """Create with rank and source."""
        chunk = ChunkRecord(id="chunk_001", text="Hello")
        result = RetrievalResult(chunk=chunk, score=0.95, rank=1, source="dense")
        assert result.rank == 1
        assert result.source == "dense"

    def test_to_dict(self):
        """Convert to dictionary."""
        chunk = ChunkRecord(id="chunk_001", text="Hello")
        result = RetrievalResult(chunk=chunk, score=0.95)
        d = result.to_dict()
        assert d["score"] == 0.95
        assert d["chunk"]["id"] == "chunk_001"


# ---------------------------------------------------------------------------
# Tests: Metadata conventions
# ---------------------------------------------------------------------------

class TestMetadataConventions:
    """Test metadata field conventions."""

    def test_document_metadata_has_source_path(self):
        """Document metadata should contain source_path."""
        doc = Document(
            id="doc_001",
            text="Hello",
            metadata={"source_path": "/path/to/file.pdf"},
        )
        assert "source_path" in doc.metadata
        assert doc.source_path == "/path/to/file.pdf"

    def test_document_metadata_images_format(self):
        """Document metadata images should follow the convention."""
        doc = Document(
            id="doc_001",
            text="[IMAGE: img_001] Hello",
            metadata={
                "source_path": "test.pdf",
                "images": [
                    {
                        "id": "img_001",
                        "path": "data/images/doc1/img_001.png",
                        "page": 1,
                        "text_offset": 0,
                        "text_length": 15,
                    }
                ],
            },
        )
        images = doc.images
        assert len(images) == 1
        assert images[0].id == "img_001"
        assert images[0].text_offset == 0
        assert images[0].text_length == 15
