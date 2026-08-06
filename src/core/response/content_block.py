"""
Content block types for the MCP response side.

This module is a thin facade over :mod:`mcp.types`. The MCP
library already provides :class:`mcp.types.TextContent` and
:class:`mcp.types.ImageContent` — exactly the shapes the
protocol layer needs. Rather than reinvent them, we re-export
them under a project-internal name (``ContentBlock``,
``TextContent``, ``ImageContent``) and add a small sentinel
exception for missing images.

Why not just import ``mcp.types`` everywhere
-------------------------------------------
Re-exporting gives us one place to evolve the project-internal
contract (e.g. add a project-specific subclass or a sentinel
type) without forcing every caller to know the MCP library
internals. It also documents *our* usage of the protocol,
not just the library's surface.

Why no Audio/Video/Pdf/Table blocks
-----------------------------------
E6's scope is "let the user see the source image". Adding
other block kinds now would be speculative — there is no
consumer and no test. When a real use case arrives, add the
block kind with its tests at that time.
"""

from __future__ import annotations

from mcp.types import ImageContent, TextContent

# Project-internal alias. ``ContentBlock`` is the union type
# the assembler emits; the underlying concrete types are
# whatever the MCP library provides.
ContentBlock = TextContent | ImageContent


__all__ = [
    "ContentBlock",
    "TextContent",
    "ImageContent",
]
