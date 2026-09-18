from __future__ import annotations

import json

from src.application.services.audit_store import AuditStore, sanitize_audit_metadata


def test_central_redaction_drops_sensitive_fields_recursively() -> None:
    safe = sanitize_audit_metadata({
        "query": "private question", "Authorization": "Bearer secret",
        "nested": {"evidence": "private body", "count": 2},
        "collection": "manuals",
    })
    assert safe == {"nested": {"count": 2}, "collection": "manuals"}


def test_append_only_chain_contains_no_sensitive_payload(tmp_path) -> None:
    store = AuditStore(tmp_path / "audit.db")
    store.append(
        actor_type="mcp_key", actor_id="key-1", action="mcp.tool.call",
        resource_type="mcp_tool", resource_id="query_knowledge_hub",
        outcome="success", metadata={"query": "secret", "result_count": 3},
        occurred_at=1,
    )
    store.append(
        actor_type="admin", actor_id="alice", action="revision.create",
        resource_type="revision", resource_id="r1", outcome="success",
        occurred_at=2,
    )
    rows = list(reversed(store.list()))
    assert json.loads(rows[0]["metadata_json"]) == {"result_count": 3}
    assert rows[1]["previous_hash"] == rows[0]["event_hash"]
    assert "secret" not in str(rows)
