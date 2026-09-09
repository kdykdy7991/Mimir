"""Unified document-parser core types (plan §5).

Migration-owned contract between the main service and the (standalone)
DocReader. These replace the free-form ``Loader`` output with an explicit,
qualified parse request and a structurally-typed parse result, so that
downstream chunking / image persistence / VLM enhancement can rely on
table/page/image semantics instead of guessing from metadata.

Contract invariants (plan §5.1/§5.2/§6)::

    ParseRequest:
      - ``file_type`` must be confirmed jointly by extension + MIME + trusted
        Magic; a raw upload filename is display/parse-hint only and must never
        become a temp path.
      - ``engine_overrides`` only accepts a server-side allow-listed key set;
        never an arbitrary module path or command string.
      - ``request_id`` allows end-to-end tracing across the service boundary.

    ParsedDocument:
      - ``markdown`` is the authoritative parse body; images have no storage
        decided here (the main service persists them and rewrites refs).
      - ``warnings``/``parse_status`` give a partial-success surface without
        throwing away otherwise-good output.

No stored-column identity / chunking concerns belong here (plan §4 division
of responsibility). This module is deliberately dependency-free (stdlib only)
so it can be imported by the client, adapters, and tests cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Parse request
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParseRequest:
    """A single request to parse one document from in-memory bytes.

    ``source_path`` is the *original/authoritative* path label for identity and
    provenance; it is not to be used as a writable temp location. The parser
    service copies bytes into its own controlled temp dir as needed.
    """

    source_path: str
    file_name: str
    file_type: str
    content: bytes
    parser_engine: str | None = None
    request_id: str | None = None
    engine_overrides: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Frozen dataclass: normalise a couple of shapes after construction.
        object.__setattr__(self, "engine_overrides", dict(self.engine_overrides))
        if not isinstance(self.content, (bytes, bytearray)):
            raise TypeError("ParseRequest.content must be bytes")
        object.__setattr__(self, "content", bytes(self.content))


# ---------------------------------------------------------------------------
# Parse result
# ---------------------------------------------------------------------------

class ParseStatus(str, Enum):
    """Outcome of a parse attempt.

    - ``SUCCESS``  — parse completed, output considered usable.
    - ``PARTIAL_SUCCESS`` — parse produced usable body but some parts failed
      (e.g. a few images dropped); not an error.
    - ``FAILED``   — parse could not produce usable output.
    """

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


@dataclass
class ParsedImage:
    """One image produced by a parser.

    Carries raw bytes and enough provenance (page, original ref) for the main
    service to persist it and rewrite the Markdown link. ``data`` must never be
    written to logs / trace / metadata verbatim (plan §6.1/§14).
    """

    filename: str
    original_ref: str
    mime_type: str
    data: bytes
    page: int | None = None
    is_original: bool = False


@dataclass
class ParsedDocument:
    """Structured parse output (Markdown + images + metadata + warnings)."""

    markdown: str
    images: list[ParsedImage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # ParseStatus.SUCCESS by default; set PARTIAL_SUCCESS / FAILED as needed.
    parse_status: ParseStatus = ParseStatus.SUCCESS

    def __post_init__(self) -> None:
        self.images = list(self.images)
        self.warnings = list(self.warnings)
        self.metadata = dict(self.metadata)