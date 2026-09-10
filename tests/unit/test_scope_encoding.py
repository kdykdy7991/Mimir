"""Delimiter-free scope encoding — regression for the P0 escalation finding."""

from __future__ import annotations

import pytest

from src.mcp_server.clients.scope import decode_scope_header, encode_scope_header


def test_roundtrip_multiple_collections():
    header = encode_scope_header(["hr", "finance"])
    assert sorted(decode_scope_header(header)) == ["finance", "hr"]


def test_single_collection_with_comma_stays_single():
    # The escalation case: one grant "finance,hr" must NOT become finance+hr.
    header = encode_scope_header(["finance,hr"])
    assert decode_scope_header(header) == ("finance,hr",)


def test_two_names_with_commas_distinct():
    header = encode_scope_header(["a,b", "c,d"])
    assert sorted(decode_scope_header(header)) == ["a,b", "c,d"]


def test_empty_returns_none():
    assert encode_scope_header([]) is None


def test_malformed_header_fails_closed():
    for bad in ("not-base64!!", "%%%", "W10="):  # W10= = "[]" must yield empty
        if bad == "W10=":
            assert decode_scope_header(bad) == ()
            continue
        with pytest.raises((ValueError, TypeError)):
            decode_scope_header(bad)


def test_non_string_payload_rejected():
    import base64
    import json
    raw = base64.urlsafe_b64encode(json.dumps([1, 2, 3]).encode()).decode()
    with pytest.raises(ValueError):
        decode_scope_header(raw)