"""Stable error vocabulary for v1 evidence responses (Task 02 §02.3).

Errors are distinct from warnings (:mod:`messaging`): warnings ride on
successful responses; errors are a stable code plane independent of
transport. Six codes are frozen now:

============================ ==========================================
code                         meaning
============================ ==========================================
invalid_request              malformed / out-of-range caller input
not_found_or_not_accessible  missing or unauthorized (existence hidden)
upstream_timeout             upstream read exceeded its time budget
upstream_unavailable         upstream connection/5xx failure
rate_limited                 caller exceeded a rate budget (Task 08
                             implements enforcement; contract only now)
overloaded                   server is over capacity (contract only)
============================ ==========================================

``rate_limited`` / ``overloaded`` are deliberately separate from
``upstream_unavailable``: a budget rejection must never be mis-reported
as an infrastructure outage.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from typing import Any

from src.application.contracts.serialization import (
    ContractError,
    require_nonempty_str,
    to_json,
    to_jsonable,
)


class ErrorCode(str, enum.Enum):
    """Stable machine-readable error codes."""

    INVALID_REQUEST = "invalid_request"
    NOT_FOUND_OR_NOT_ACCESSIBLE = "not_found_or_not_accessible"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"


# Codes the caller may sensibly retry, and whether Retry-After applies.
RETRYABLE_CODES = frozenset({
    ErrorCode.UPSTREAM_TIMEOUT,
    ErrorCode.UPSTREAM_UNAVAILABLE,
    ErrorCode.RATE_LIMITED,
    ErrorCode.OVERLOADED,
})


@dataclass(frozen=True)
class ErrorV1:
    """Transport-neutral structured error."""

    code: ErrorCode
    message: str
    retryable: bool = False
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, ErrorCode):
            if isinstance(self.code, str):
                try:
                    object.__setattr__(self, "code", ErrorCode(self.code))
                except ValueError as exc:
                    raise ContractError(
                        f"unknown error code: {self.code!r}",
                    ) from exc
            else:
                raise ContractError("error code must be an ErrorCode value")
        object.__setattr__(
            self, "message", require_nonempty_str(self.message, "message"),
        )
        if self.retry_after_seconds is not None:
            value = self.retry_after_seconds
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ContractError("retry_after_seconds must be a number")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ContractError(
                    "retry_after_seconds must be a finite positive number",
                )
            object.__setattr__(self, "retry_after_seconds", float(value))
        if not isinstance(self.retryable, bool):
            raise ContractError("retryable must be a boolean")
        # Consistency, not a requirement callers set by hand.
        if self.code in RETRYABLE_CODES and not self.retryable:
            object.__setattr__(self, "retryable", True)

    @classmethod
    def from_mapping(cls, data: Any) -> ErrorV1:
        if isinstance(data, ErrorV1):
            return data
        if not isinstance(data, dict):
            raise ContractError("error must be a mapping")
        return cls(
            code=data["code"],
            message=data["message"],
            retryable=bool(data.get("retryable", False)),
            retry_after_seconds=data.get("retry_after_seconds"),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_json(self) -> str:
        return to_json(self)
