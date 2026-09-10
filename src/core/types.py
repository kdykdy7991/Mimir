"""
Core data types/contracts for the RAG pipeline.

Shared across ingestion → retrieval → mcp tools.
Avoids coupling and duplication across submodules.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# Image reference in Document/Chunk metadata
# ---------------------------------------------------------------------------

@dataclass
class ImageRef:
    """
    Image reference in document metadata.

    Used to track images extracted from documents (e.g., PDF).
    """
    id: str                    # Global unique image ID: {doc_hash}_{page}_{seq}
    path: str                  # Storage path: data/images/{collection}/{image_id}.png
    page: int | None = None    # Page number in source document
    text_offset: int = 0       # Start position of placeholder in Document.text
    text_length: int = 0       # Length of placeholder (e.g., len("[IMAGE: {id}]"))
    position: dict[str, Any] | None = None  # Physical position (PDF coordinates, etc.)
    is_content: bool = True    # Whether the image carries informational value
    classification_reason: str | None = None  # Why it was marked decorative (if not content)
    data: bytes | None = None  # Inline raw bytes (docreader path); carried from load to
                               # pipeline Stage 2.5 for persistence, then cleared.
    mime_type: str | None = None  # image MIME (e.g. image/png); drives the stored ext.

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageRef:
        """Create from dictionary."""
        return cls(**data)


# ---------------------------------------------------------------------------
# Document: Raw document after loading
# ---------------------------------------------------------------------------

@dataclass
class Document:
    """
    Raw document after loading.

    Represents a single document (e.g., PDF file) with text content
    and metadata. Images are referenced via metadata.images.
    """
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    # Convenience properties
    @property
    def source_path(self) -> str:
        """Get source file path from metadata."""
        return self.metadata.get("source_path", "")

    @property
    def images(self) -> list[ImageRef]:
        """Get image references from metadata."""
        images_data = self.metadata.get("images", [])
        return [ImageRef.from_dict(img) if isinstance(img, dict) else img
                for img in images_data]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (JSON-serializable)."""
        return {
            "id": self.id,
            "text": self.text,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        """Create from dictionary."""
        return cls(**data)


# ---------------------------------------------------------------------------
# Chunk: Text chunk after splitting
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """
    Text chunk after splitting.

    Represents a piece of text split from a Document,
    with position information for溯源.
    """
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    start_offset: int = 0      # Start offset in original document
    end_offset: int = 0        # End offset in original document
    source_ref: str | None = None  # Reference to source Document.id

    @property
    def source_path(self) -> str:
        """Get source file path from metadata."""
        return self.metadata.get("source_path", "")

    @property
    def images(self) -> list[ImageRef]:
        """Get image references from metadata."""
        images_data = self.metadata.get("images", [])
        return [ImageRef.from_dict(img) if isinstance(img, dict) else img
                for img in images_data]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (JSON-serializable)."""
        return {
            "id": self.id,
            "text": self.text,
            "metadata": self.metadata,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chunk:
        """Create from dictionary."""
        return cls(**data)


# ---------------------------------------------------------------------------
# ChunkRecord: Storage/retrieval carrier
# ---------------------------------------------------------------------------

@dataclass
class ChunkRecord:
    """
    Storage/retrieval carrier for chunks.

    Extends Chunk with vector fields for storage and retrieval.
    Used by VectorStore and BM25Indexer.
    """
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    start_offset: int = 0
    end_offset: int = 0
    source_ref: str | None = None
    dense_vector: list[float] | None = None   # For vector store
    sparse_vector: dict[str, float] | None = None  # For BM25 (term -> weight)

    @classmethod
    def from_chunk(cls, chunk: Chunk, **kwargs) -> ChunkRecord:
        """Create ChunkRecord from Chunk."""
        return cls(
            id=chunk.id,
            text=chunk.text,
            metadata=chunk.metadata,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            source_ref=chunk.source_ref,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (JSON-serializable)."""
        return {
            "id": self.id,
            "text": self.text,
            "metadata": self.metadata,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "source_ref": self.source_ref,
            "dense_vector": self.dense_vector,
            "sparse_vector": self.sparse_vector,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkRecord:
        """Create from dictionary."""
        return cls(**data)


# ---------------------------------------------------------------------------
# ProcessedQuery: Query after preprocessing
# ---------------------------------------------------------------------------

@dataclass
class ProcessedQuery:
    """
    Query after preprocessing.

    Contains the original query, extracted keywords, and filters.
    """
    original: str              # Original user query
    keywords: list[str] = field(default_factory=list)  # Extracted keywords
    filters: dict[str, Any] = field(default_factory=dict)  # Metadata filters
    expanded: str | None = None  # Expanded/rewritten query (optional)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "original": self.original,
            "keywords": self.keywords,
            "filters": self.filters,
            "expanded": self.expanded,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessedQuery:
        """Create from dictionary."""
        return cls(**data)


# ---------------------------------------------------------------------------
# RetrievalResult: Single retrieval result
# ---------------------------------------------------------------------------

@dataclass
class RetrievalResult:
    """
    Single retrieval result.

    Contains the chunk and its relevance score.

    The flat fields ``chunk_id`` / ``text`` / ``metadata`` below are
    convenience accessors that delegate to ``self.chunk.*`` — they
    exist so the D2 retriever API (``result.chunk_id``,
    ``result.text``, ``result.metadata``) matches the spec
    without duplicating the chunk fields here. Both styles work
    interchangeably.
    """
    chunk: ChunkRecord
    score: float               # Relevance score
    rank: int = 0              # Rank in result list
    source: str = ""           # Retrieval source: "dense", "sparse", "fusion", "rerank"

    # ------------------------------------------------------------------
    # Convenience accessors (delegate to the embedded chunk).
    # ------------------------------------------------------------------
    @property
    def chunk_id(self) -> str:
        return self.chunk.id

    @property
    def text(self) -> str:
        return self.chunk.text

    @property
    def metadata(self) -> dict[str, Any]:
        return self.chunk.metadata

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "chunk": self.chunk.to_dict(),
            "score": self.score,
            "rank": self.rank,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetrievalResult:
        """Create from dictionary."""
        return cls(
            chunk=ChunkRecord.from_dict(data["chunk"]),
            score=data["score"],
            rank=data.get("rank", 0),
            source=data.get("source", ""),
        )
