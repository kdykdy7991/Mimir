"""Reviewable enrichment contracts (Task 07.6)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime


class SuggestionStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class TagSuggestion:
    suggestion_id: str
    collection: str
    document_id: str
    revision_id: str
    suggested_name: str
    existing_tag_id: str | None
    model: str
    prompt_version: str
    confidence: float
    status: SuggestionStatus
    created_at: datetime
    reviewed_at: datetime | None = None
    reviewer: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "suggestion_id", "collection", "document_id", "revision_id",
            "suggested_name", "model", "prompt_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("confidence must be a number")
        if not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not isinstance(self.status, SuggestionStatus):
            object.__setattr__(self, "status", SuggestionStatus(str(self.status)))


__all__ = ["SuggestionStatus", "TagSuggestion"]
