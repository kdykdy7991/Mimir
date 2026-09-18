"""Three collection-scoped, read-only datasource governance tools."""

from __future__ import annotations

from typing import Any

from src.mcp_server.auth.context import current_principal
from src.mcp_server.clients.errors import ResourceNotFoundError
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error
from src.mcp_server.tools.common import client_from_args


async def _list(args: dict[str, Any]):
    items = client_from_args(args).list_data_sources(current_principal())
    data = [{
        "id": item.id, "name": item.name, "connector_type": item.connector_type,
        "collection_id": item.collection_id, "enabled": item.enabled,
        "checkpoint_revision": item.checkpoint_revision, "updated_at": item.updated_at,
    } for item in items]
    return f"# Data sources\n\n{len(data)} authorized source(s).", {"count": len(data), "data_sources": data}


async def _status(args: dict[str, Any]):
    try:
        value = client_from_args(args).get_sync_status(str(args.get("source_id", "")), current_principal())
    except ResourceNotFoundError:
        return tool_error("data source not found or not accessible")
    return f"# Sync status\n\n{value.data_source.name}", {
        "data_source": value.data_source.__dict__, "last_run": value.last_run,
    }


async def _failures(args: dict[str, Any]):
    try:
        items = client_from_args(args).list_sync_failures(
            str(args.get("source_id", "")), int(args.get("limit", 20)), current_principal(),
        )
    except (ResourceNotFoundError, ValueError):
        return tool_error("data source not found or not accessible")
    return f"# Sync failures\n\n{len(items)} failure(s).", {"count": len(items), "failures": items}


def register(handler: ProtocolHandler) -> None:
    handler.register(name="list_data_sources", description="List datasource metadata authorized for the current key.", input_schema={"type":"object","properties":{},"additionalProperties":False}, handler=_list, output_schema={"type":"object","properties":{"count":{"type":"integer"},"data_sources":{"type":"array","items":{"type":"object"}}},"required":["count","data_sources"]})
    handler.register(name="get_sync_status", description="Read the latest status for one authorized datasource.", input_schema={"type":"object","properties":{"source_id":{"type":"string"}},"required":["source_id"],"additionalProperties":False}, handler=_status, output_schema={"type":"object","properties":{"data_source":{"type":"object"},"last_run":{"type":["object","null"]}},"required":["data_source","last_run"]})
    handler.register(name="list_sync_failures", description="List bounded failures for one authorized datasource.", input_schema={"type":"object","properties":{"source_id":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":100,"default":20}},"required":["source_id"],"additionalProperties":False}, handler=_failures, output_schema={"type":"object","properties":{"count":{"type":"integer"},"failures":{"type":"array","items":{"type":"object"}}},"required":["count","failures"]})


__all__ = ["register"]
