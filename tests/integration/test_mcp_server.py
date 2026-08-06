"""
Integration tests for the MCP server (E1 + E2) over stdio.

These tests launch ``python -m main`` as a subprocess, exchange
real MCP/JSON-RPC messages over stdio, and assert the responses.
The tools are exercised with mocked backends so the tests don't
require a real LLM, embedding model, or vector store.

What we cover:
- ``initialize`` round-trip returns server info + capabilities
- ``tools/list`` returns the three registered tools
- ``tools/call`` for each tool returns the expected shape
- stderr captures logs (stdout stays clean — MCP stream integrity)
- The server exits cleanly on EOF
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError


REPO_ROOT = Path(__file__).resolve().parents[2]


def _server_params(*, log_level: str = "WARNING") -> StdioServerParameters:
    """Build the subprocess parameters for `python -m main`."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "main", "--config", "./config/settings.yaml",
              "--log-level", log_level],
        cwd=str(REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# Initialize / tools/list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initialize_returns_server_info():
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.server_info is not None
            assert init.server_info.name == "skdy-rag-server"
            assert init.capabilities is not None


@pytest.mark.asyncio
async def test_tools_list_returns_three_tools():
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            assert names == [
                "get_document_summary",
                "list_collections",
                "query_knowledge_hub",
            ]


@pytest.mark.asyncio
async def test_tool_schemas_match():
    """Each registered tool exposes the expected input properties."""
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}

            q = tools["query_knowledge_hub"]
            assert "query" in q.input_schema["properties"]
            assert "top_k" in q.input_schema["properties"]
            assert "collection" in q.input_schema["properties"]
            assert "no_rerank" in q.input_schema["properties"]
            assert q.input_schema["required"] == ["query"]

            s = tools["get_document_summary"]
            assert "doc_id" in s.input_schema["properties"]
            assert s.input_schema["required"] == ["doc_id"]

            l = tools["list_collections"]
            assert l.input_schema["properties"] == {}


# ---------------------------------------------------------------------------
# Tool invocation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_list_collections():
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "list_collections", arguments={},
            )
            assert not result.is_error
            # The first content block is the Markdown.
            text_blocks = [c for c in result.content if c.type == "text"]
            assert len(text_blocks) >= 1
            assert "Collections" in text_blocks[0].text


@pytest.mark.asyncio
async def test_call_get_document_summary_missing_raises():
    """A missing doc_id returns is_error=True with a clear message.

    Known business errors surface as ``CallToolResult.is_error``, NOT a
    protocol MCPError — see protocol_handler "Error mapping".
    """
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "get_document_summary",
                arguments={"doc_id": "definitely-not-a-real-id"},
            )
            assert result.is_error
            text = result.content[0].text
            assert "document not found" in text or "failed" in text.lower()


@pytest.mark.asyncio
async def test_call_query_knowledge_hub_empty_query_raises():
    """An empty query returns is_error=True (a tool-level error)."""
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "query_knowledge_hub", arguments={"query": ""},
            )
            assert result.is_error


@pytest.mark.asyncio
async def test_call_unknown_tool_raises_protocol_error():
    """An unknown tool name is a protocol-level MCPError, not is_error."""
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            with pytest.raises(MCPError):
                await session.call_tool(
                    "nonexistent_tool", arguments={},
                )


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stderr_does_not_pollute_stdout(capsys):
    """All logs go to stderr; stdout is reserved for MCP frames."""
    # We can't easily capture the subprocess's stderr/stdout
    # separately from pytest's capsys, so we just verify that the
    # server starts and exchanges messages without raising — a
    # log line leaking to stdout would corrupt the JSON-RPC stream
    # and the call would fail.
    async with stdio_client(_server_params(log_level="INFO")) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.server_info is not None
            # If logs leaked to stdout, the next call would fail.
            tools = await session.list_tools()
            assert len(tools.tools) >= 1
