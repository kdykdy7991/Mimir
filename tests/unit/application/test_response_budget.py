"""
Task 02.3 — application ResponseBudget and stable error vocabulary.

Covers: frozen legacy defaults, startup-style validation failures,
request-bound boundary semantics, the six ErrorCode values, ErrorV1
consistency and serialization.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from src.application.contracts import (
    ContractError,
    ErrorCode,
    ErrorV1,
    ResponseBudget,
    to_json,
)


# ---------------------------------------------------------------------------
# Defaults — must match the five legacy tools' published limits.
# ---------------------------------------------------------------------------

def test_default_budget_freezes_legacy_limits():
    budget = ResponseBudget()
    assert budget.query_max_length == 2000
    assert budget.top_k_default == 10
    assert budget.top_k_max == 50
    assert budget.page_size_default == 20
    assert budget.page_size_max == 50
    assert budget.max_evidence_count == 50
    assert budget.max_preview_chars == 200
    # Reserved Task 05 knobs exist but are not exposed on tools.
    assert budget.alternate_query_max_count == 3
    assert budget.alternate_query_total_max_chars == 3000


def test_budget_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ResponseBudget().top_k_max = 10  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Startup validation (fail-fast)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"query_max_length": 0},
    {"top_k_default": 51},
    {"page_size_default": 51},
    {"max_evidence_count": 10},      # < top_k_max
    {"max_preview_chars": 9000},      # > content cap
    {"max_content_chars": 300_000},   # > structured cap
    {"alternate_query_total_max_chars": 10},
    {"top_k_max": -1},
    {"top_k_default": True},
])
def test_invalid_budgets_fail_fast(kwargs):
    with pytest.raises(ContractError):
        ResponseBudget(**kwargs)


# ---------------------------------------------------------------------------
# Request bounds — boundary values + stable messages
# ---------------------------------------------------------------------------

def test_query_length_boundaries_and_messages():
    budget = ResponseBudget()
    budget.check_query_length("x" * 2000)
    with pytest.raises(ContractError) as exc:
        budget.check_query_length("x" * 2001)
    assert "2000-character limit" in str(exc.value)
    with pytest.raises(ContractError) as exc:
        budget.check_query_length("")
    assert "'query' is required" in str(exc.value)
    with pytest.raises(ContractError):
        budget.check_query_length("   ")


def test_top_k_boundaries_and_bools_rejected():
    budget = ResponseBudget()
    for good in (1, 25, 50):
        budget.check_top_k(good)
    for bad in (0, 51, -3, True):
        with pytest.raises(ContractError):
            budget.check_top_k(bad)  # type: ignore[arg-type]


def test_page_and_page_size_boundaries_keep_legacy_wording():
    budget = ResponseBudget()
    budget.check_page(1)
    budget.check_page_size(1)
    budget.check_page_size(50)
    with pytest.raises(ContractError) as exc:
        budget.check_page_size(51)
    assert str(exc.value) == "page_size must be between 1 and 50"
    with pytest.raises(ContractError) as exc:
        budget.check_page(0)
    assert str(exc.value) == "page must be >= 1"
    with pytest.raises(ContractError):
        budget.check_page_size(True)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Error vocabulary
# ---------------------------------------------------------------------------

def test_error_codes_are_the_six_stable_values():
    assert [c.value for c in ErrorCode] == [
        "invalid_request",
        "not_found_or_not_accessible",
        "upstream_timeout",
        "upstream_unavailable",
        "rate_limited",
        "overloaded",
    ]


def test_error_v1_serialization_and_retryable_consistency():
    err = ErrorV1(code=ErrorCode.RATE_LIMITED, message="slow down",
                  retry_after_seconds=30)
    payload = json.loads(to_json(err))
    assert payload == {
        "code": "rate_limited",
        "message": "slow down",
        "retryable": True,
        "retry_after_seconds": 30.0,
    }
    # retryable flips on for the retryable codes even if omitted.
    assert ErrorV1(code=ErrorCode.OVERLOADED, message="busy").retryable is True
    # Non-retryable codes stay non-retryable.
    assert ErrorV1(
        code=ErrorCode.INVALID_REQUEST, message="bad",
    ).retryable is False


def test_error_v1_rejects_unknown_code_bad_retry_after_and_empty_message():
    with pytest.raises(ContractError):
        ErrorV1(code="nope", message="x")
    with pytest.raises(ContractError):
        ErrorV1(code=ErrorCode.RATE_LIMITED, message="x",
                retry_after_seconds=0)
    with pytest.raises(ContractError):
        ErrorV1(code=ErrorCode.INVALID_REQUEST, message=" ")


def test_rate_limited_is_distinct_from_upstream_unavailable():
    assert ErrorCode.RATE_LIMITED.value != "upstream_unavailable"
    assert ErrorCode.OVERLOADED.value != "upstream_unavailable"
