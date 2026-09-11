"""B4.2 — connection diagnostics classification unit tests."""

from __future__ import annotations

import pytest

from src.web_api.mcp_connection import classify_normalized, _REVOKED, _UNAUTHORIZED, resolve_auth_failure
from src.web_api.mcp_diagnostics import (
    ConnectionDiagnostics,
    all_diagnostics,
    diagnostic_for,
)

EXPECTED_CODES = {
    "server_unreachable",
    "handshake_failed",
    "client_unauthorized",
    "client_key_revoked",
    "internal_auth_failed",
    "upstream_unavailable",
    "empty_scope",
    "timeout",
    "unexpected_response",
}


def test_exactly_nine_stable_codes():
    assert {str(c.value) for c in ConnectionDiagnostics} == EXPECTED_CODES


def test_mapping_is_deterministic_and_user_safe():
    infos = list(all_diagnostics())
    # one entry per enum, stable order
    assert len(infos) == 9
    seen = set()
    for info in infos:
        assert info.code in EXPECTED_CODES
        assert info.message
        assert info.suggested_action
        assert info.code not in seen
        seen.add(info.code)


def test_messages_never_leak_internals():
    for info in all_diagnostics():
        combined = f"{info.message} {info.suggested_action}"
        # no stack traces, paths, credentials, or exception markers
        assert "Traceback" not in combined
        assert "skdy_mcp_" not in combined
        assert "MCP_INTERNAL_API_KEY" not in combined
        assert "Authorization" not in combined
        assert "/data/" not in combined
        assert combined.count("{") == combined.count("}")


def test_normalized_mapping_covers_all_enum_values():
    # every enum value is reachable via some normalized key: assert the map
    # produces every B4.2 code without surfacing internals.
    from src.web_api import mcp_connection as mc

    produced = set()
    for key in mc._NORMALIZED_CODES:
        produced.add(classify_normalized(key).code)
    assert produced == EXPECTED_CODES


def test_diagnostic_for_by_string_and_enum():
    assert diagnostic_for("timeout") == diagnostic_for(ConnectionDiagnostics.TIMEOUT)


def test_unknown_normalized_reason_fails_fast():
    with pytest.raises(KeyError):
        classify_normalized("no_such_reason")


class _FakeKeyService:
    """Metadata-only stand-in for the ApiKeyService used in classification."""

    def __init__(self, keys):
        self._keys = keys

    def list_keys(self):
        return list(self._keys)


def test_auth_failure_revoked_key():
    from src.mcp_server.auth.models import ApiKeyMetadata
    from datetime import datetime, timezone

    keys = [
        ApiKeyMetadata(
            key_id="revokedid", name="r", allowed_collections=frozenset({"a"}),
            enabled=False, created_at=datetime.now(timezone.utc),
            revoked_at=datetime.now(timezone.utc), last_used_at=None,
        ),
    ]
    svc = _FakeKeyService(keys)
    # 9-byte url-safe key_id → token_urlsafe(9) is 12 chars; craft a plausible raw key
    info = resolve_auth_failure("skdy_mcp_revokedid.some_secret_value", svc)
    assert info.code == ConnectionDiagnostics.CLIENT_KEY_REVOKED.value


def test_auth_failure_wrong_secret_on_enabled_key():
    from src.mcp_server.auth.models import ApiKeyMetadata
    from datetime import datetime, timezone

    keys = [
        ApiKeyMetadata(
            key_id="activeid", name="a", allowed_collections=frozenset({"a"}),
            enabled=True, created_at=datetime.now(timezone.utc),
            revoked_at=None, last_used_at=None,
        ),
    ]
    # key_id found but enabled → the secret must be wrong → unauthorized
    info = resolve_auth_failure("skdy_mcp_activeid.wrong_secret_value", _FakeKeyService(keys))
    assert info.code == ConnectionDiagnostics.CLIENT_UNAUTHORIZED.value


def test_auth_failure_unknown_key():
    info = resolve_auth_failure("skdy_mcp_unknownkeyid.some_secret_value", _FakeKeyService([]))
    assert info.code == ConnectionDiagnostics.CLIENT_UNAUTHORIZED.value


def test_classify_normalized_revoked_authorized():
    assert classify_normalized(_REVOKED).code == "client_key_revoked"
    assert classify_normalized(_UNAUTHORIZED).code == "client_unauthorized"