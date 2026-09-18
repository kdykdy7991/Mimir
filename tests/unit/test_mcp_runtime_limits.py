from __future__ import annotations

import asyncio

from src.application.services.resource_limits import WorkloadBudget, WorkloadLimiter
from src.mcp_server.auth.context import TrustedLocalPrincipal
from src.mcp_server.protocol_handler import ProtocolHandler


def _handler(limiter: WorkloadLimiter) -> ProtocolHandler:
    handler = ProtocolHandler(limiter=limiter)
    handler.register(
        name="read", description="read", input_schema={"type": "object"},
        handler=lambda args: asyncio.sleep(0, result="ok"),
    )
    return handler


async def test_mcp_rate_limit_is_stable_tool_error_per_key() -> None:
    limiter = WorkloadLimiter({
        "mcp": WorkloadBudget(concurrency=2, requests_per_minute=1),
    })
    handler = _handler(limiter)
    principal = TrustedLocalPrincipal(key_id="key-a", name="a")
    await handler.dispatch("read", principal=principal)
    rejected = await handler.dispatch("read", principal=principal)
    assert rejected.is_error is True
    assert rejected.content[0].text.startswith("rate_limited:")
    allowed = await handler.dispatch(
        "read", principal=TrustedLocalPrincipal(key_id="key-b", name="b"),
    )
    assert allowed[0].text == "ok"
