"""Traceable derived summaries and synthetic questions (Task 07.7)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime


class DerivedKind(str, enum.Enum):
    SUMMARY = "summary"
    SYNTHETIC_QUESTION = "synthetic_question"


@dataclass(frozen=True)
class DerivedArtifact:
    artifact_id: str
    kind: DerivedKind
    collection: str
    document_id: str
    chunk_id: str
    revision_id: str
    text: str
    model: str
    prompt_version: str
    weight: float
    enabled: bool
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DerivedKind):
            object.__setattr__(self, "kind", DerivedKind(str(self.kind)))
        if not self.text.strip():
            raise ValueError("derived text must not be empty")
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)):
            raise ValueError("derived weight must be numeric")
        if not 0 <= float(self.weight) <= 10:
            raise ValueError("derived weight must be between 0 and 10")


__all__ = ["DerivedArtifact", "DerivedKind"]
