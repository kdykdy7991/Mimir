"""Transport-neutral v1 contracts for knowledge evidence (Task 02).

This package is owned by the **application layer**. It defines the
shared vocabulary for future read/retrieval tools — evidence, filters,
warnings, pagination/budget envelopes, errors and request budgets —
with no dependency on the MCP SDK, FastAPI/Web DTOs, Chroma, BM25 or
any LLM types (ADR 0001 §3.3).

Transport-specific shaping (MCP structured content, REST DTOs) is built
on top of these contracts; never the other way around.
"""

from __future__ import annotations

from src.application.contracts.budget import ResponseBudget
from src.application.contracts.errors import ErrorCode, ErrorV1, RETRYABLE_CODES
from src.application.contracts.evidence import (
    EvidenceScores,
    EvidenceV1,
    SourceLocator,
)
from src.application.contracts.filters import EvidenceFilterV1, TagOperator
from src.application.contracts.messaging import (
    EvidencePageV1,
    WarningCode,
    WarningV1,
)
from src.application.contracts.serialization import (
    ContractError,
    deduplicated,
    parse_datetime,
    to_json,
    to_jsonable,
)

CONTRACT_VERSION = "evidence-v1"

__all__ = [
    "CONTRACT_VERSION",
    "ContractError",
    "ResponseBudget",
    "ErrorCode",
    "ErrorV1",
    "RETRYABLE_CODES",
    "EvidenceScores",
    "EvidenceV1",
    "SourceLocator",
    "EvidenceFilterV1",
    "TagOperator",
    "EvidencePageV1",
    "WarningCode",
    "WarningV1",
    "deduplicated",
    "parse_datetime",
    "to_json",
    "to_jsonable",
]
