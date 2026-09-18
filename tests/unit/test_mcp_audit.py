from __future__ import annotations

import asyncio

from src.application.services.audit_store import AuditStore
from src.mcp_server.protocol_handler import ProtocolHandler, tool_error


async def test_mcp_calls_audit_outcome_without_arguments(tmp_path) -> None:
    audit = AuditStore(tmp_path / "audit.db")
    handler = ProtocolHandler(audit_store=audit)
    handler.register(
        name="read", description="read", input_schema={},
        handler=lambda args: asyncio.sleep(
            0, result=tool_error("not_found: unavailable") if args.get("deny") else "ok",
        ),
    )
    await handler.dispatch("read", {"query": "must-not-log"})
    await handler.dispatch("read", {"deny": True, "Authorization": "secret"})
    rows = list(reversed(audit.list()))
    assert [row["outcome"] for row in rows] == ["success", "denied"]
    assert "must-not-log" not in str(rows)
    assert "secret" not in str(rows)
