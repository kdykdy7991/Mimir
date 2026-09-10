# SPDX-License-Identifier: MIT
# Phase 6 (legacy DOC/XLS/PPT #4): availability-gated backend contract. There is
# no local converter offline, so these assertions verify that the engine is
# *advertised as unavailable* and refuses to fabricate output (no fake success).
"""Tests for the availability-gated legacy Office parser."""

from __future__ import annotations

import pytest

from docreader.parser.legacy_office_parser import LegacyOfficeParser, legacy_office_available
from docreader.parser.registry import list_engines, parse_file


def test_legacy_office_advertises_with_reason() -> None:
    ok, reason = legacy_office_available()
    builtin = None
    for e in list_engines():
        if e["name"] == "opendataloader" and "doc" in e["file_types"]:
            builtin = e
    # Offline, the ODL converter is not present -> advertised unavailable.
    assert not ok
    assert reason
    assert builtin is not None
    assert builtin["available"] is False
    assert builtin["unavailable_reason"]


def test_legacy_office_registered_for_doc_xls_ppt() -> None:
    odl = next(e for e in list_engines() if e["name"] == "opendataloader")
    assert {"doc", "xls", "ppt"}.issubset(set(odl["file_types"]))


def test_legacy_office_parse_refuses_when_unavailable() -> None:
    # never an empty placeholder marked as success
    for ext in (".doc", ".xls", ".ppt"):
        with pytest.raises(ValueError):
            LegacyOfficeParser().parse(b"not really content but a binary blob")
        with pytest.raises(ValueError):
            parse_file("a" + ext, ext.lstrip("."), b"\x00\x01\x02")