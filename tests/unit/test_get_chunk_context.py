from __future__ import annotations

import asyncio

from src.application.contracts import ChunkContextResult, ContextChunkV1
from src.mcp_server.clients.errors import ResourceNotFoundError
from src.mcp_server.tools.get_chunk_context import _get_chunk_context


class Client:
    def __init__(self, *, fail=False):
        self.request = None
        self.fail = fail

    def get_chunk_context(self, request, principal):
        self.request = request
        if self.fail:
            raise ResourceNotFoundError("hidden")
        return ChunkContextResult(
            document_id=request.document_id,
            hit=ContextChunkV1(chunk_id=request.chunk_id, relation="hit", text="hit"),
            parent=ContextChunkV1(chunk_id="p", relation="parent", text="parent"),
            neighbors=(ContextChunkV1(chunk_id="n", relation="after", text="next"),),
            truncated=True,
        )


def test_context_tool_maps_request_and_result():
    client = Client()
    markdown, payload = asyncio.run(_get_chunk_context({
        "document_id": "d", "chunk_id": "c", "include": "both",
        "before": 2, "after": 3, "max_chars": 100, "_client": client,
    }))
    assert client.request.before == 2
    assert client.request.after == 3
    assert payload["parent"]["chunk_id"] == "p"
    assert payload["truncated"] is True
    assert "parent" in markdown


def test_context_tool_rejects_bad_bounds_and_hides_presence():
    assert asyncio.run(_get_chunk_context({
        "document_id": "d", "chunk_id": "c", "before": 11,
    })).is_error
    result = asyncio.run(_get_chunk_context({
        "document_id": "d", "chunk_id": "c", "_client": Client(fail=True),
    }))
    assert result.is_error
    assert "not accessible" in result.content[0].text
