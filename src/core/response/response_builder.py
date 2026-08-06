"""
Response Builder — turns retrieval results into an MCP-friendly
response.

Three outputs (all derived from the same data, so they cannot
drift):

1. ``markdown`` — a human-readable Markdown string with inline
   reference markers like ``[1]`` and a trailing references
   section listing each cited chunk. Kept for callers that
   prefer a single text blob (e.g. CLI / quick log).
2. ``structured`` — a dict of the same information in a shape
   that MCP clients (e.g. newer Claude Desktop) can render as a
   separate side panel. Mirrored into MCP's
   ``structuredContent``.
3. ``content`` — a flat list of MCP content blocks (text +
   base64 image), so clients that consume the structured MCP
   ``content`` array directly can render inline images. See E6.

The Markdown body is the raw top-k chunk excerpts joined
together — the project does not yet ship an LLM-based "answer
synthesis" step (that would belong here once D7/H is wired). When
no results are available, the response is a friendly empty-state
message rather than an empty string, so the client never sees
``""`` and assumes a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp.types import TextContent

from src.core.response.citation_generator import Citation, generate
from src.core.response.multimodal_assembler import (
    ContentBlock,
    ImageNotFoundError,
    MultimodalAssembler,
)
from src.core.types import RetrievalResult


_EMPTY_HINT = (
    "未找到相关文档。请确认已运行 ingest.py 完成数据入库，"
    "或尝试调整 query / top_k。"
)


@dataclass
class MCPResponse:
    """Triple of (markdown, structured, content) outputs for an MCP tool call."""
    markdown: str
    structured: dict[str, Any]
    content: list[ContentBlock] = field(default_factory=list)

    def as_pair(self) -> tuple[str, dict[str, Any]]:
        """
        Return in the (str, dict) shape ``ProtocolHandler`` accepts
        for the text-only legacy path.

        The ``content`` field is intentionally NOT included here —
        callers that want the rich content list should use
        :meth:`as_content_pair` instead. The legacy pair keeps the
        single-text-block MCP shape that older clients rely on.
        """
        return self.markdown, self.structured

    def as_content_pair(self) -> tuple[list[ContentBlock], dict[str, Any]]:
        """
        Return in the (list[ContentBlock], dict) shape
        ``ProtocolHandler`` accepts for the multimodal path (E6).

        The :class:`ProtocolHandler` already routes a 2-tuple whose
        first element is a list into MCP's ``content`` field
        unchanged, so the images render inline for clients that
        understand the MCP image content type (e.g. Claude
        Desktop).

        When there are no content blocks (empty result set), fall back
        to the Markdown message so the client always receives at least
        one ``TextContent`` — otherwise the "未找到相关文档" empty-state
        would be silently dropped from the wire.
        """
        content = self.content or [
            TextContent(type="text", text=self.markdown),
        ]
        return content, self.structured


def _markdown_body(citations: list[Citation]) -> str:
    """Render the body — top-k excerpts separated by blank lines."""
    if not citations:
        return _EMPTY_HINT
    blocks: list[str] = []
    for c in citations:
        header = f"**[{c.index}] {c.source}**"
        if c.page is not None:
            header += f" (page {c.page})"
        blocks.append(f"{header}\n\n{c.text_excerpt}")
    return "\n\n---\n\n".join(blocks)


def _markdown_references(citations: list[Citation]) -> str:
    """Trailing references section so the user can map [n] → source."""
    if not citations:
        return ""
    lines = ["", "## References", ""]
    for c in citations:
        meta = f"p.{c.page}" if c.page is not None else "n/a"
        lines.append(
            f"[{c.index}] `{c.chunk_id}` — {c.source} "
            f"({meta}, score={c.score:.4f}, via {c.source_type})",
        )
    return "\n".join(lines)


def _structured(citations: list[Citation], query: str) -> dict[str, Any]:
    return {
        "query": query,
        "n_results": len(citations),
        "citations": [c.to_dict() for c in citations],
    }


def build(
    results: list[RetrievalResult],
    query: str,
    *,
    assembler: MultimodalAssembler | None = None,
) -> MCPResponse:
    """
    Build the full MCP response for a retrieval result list.

    The same ``query`` string is mirrored into the structured
    payload so the client can render the question alongside the
    citations.

    The ``content`` field is populated by ``assembler`` when
    provided. Pass ``assembler=None`` (default) to skip
    multimodal assembly — used by callers that don't have an
    :class:`ImageStorage` wired in (e.g. some tests).

    Missing ``image_id`` references raise
    :class:`ImageNotFoundError` from the assembler; this
    function does not catch it — the caller is expected to
    decide whether to surface the error or fall back to a
    text-only response.
    """
    citations = generate(results)
    body = _markdown_body(citations)
    refs = _markdown_references(citations)
    markdown = body + refs if citations else body
    content: list[ContentBlock] = (
        assembler.assemble(results) if assembler is not None else []
    )
    return MCPResponse(
        markdown=markdown,
        structured=_structured(citations, query),
        content=content,
    )


__all__ = ["MCPResponse", "build"]
