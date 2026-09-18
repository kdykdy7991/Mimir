"""Generate removable derived summaries/questions with source-only citations."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from src.application.contracts import ChunkRevision, DerivedArtifact, DerivedKind
from src.ingestion.storage import DerivedContentStore


class DerivedContentService:
    def __init__(
        self, store: DerivedContentStore,
        generator: Callable[[str], Mapping[str, Any]], *,
        model: str, prompt_version: str, enabled: bool = False,
        question_weight: float = 0.7,
    ) -> None:
        self.store = store
        self.generator = generator
        self.model = model
        self.prompt_version = prompt_version
        self.enabled = enabled
        self.question_weight = question_weight

    def rebuild(self, revision: ChunkRevision) -> tuple[DerivedArtifact, ...]:
        if not self.enabled:
            self.store.replace_revision(revision.revision_id, [])
            return ()
        try:
            rows = self.generate(revision)
        except Exception:
            return tuple(self.store.list(revision.revision_id))
        self.store.replace_revision(revision.revision_id, list(rows))
        return rows

    def generate(self, revision: ChunkRevision) -> tuple[DerivedArtifact, ...]:
        """Build artifacts without persistence for transactional staging."""
        if not self.enabled:
            return ()
        payload = self.generator(revision.text)
        summary = str(payload.get("summary") or "").strip()
        raw_questions = payload.get("questions") or []
        if not isinstance(raw_questions, list):
            raise ValueError("derived questions must be a list")
        rows: list[DerivedArtifact] = []
        if summary:
            rows.append(self._artifact(revision, DerivedKind.SUMMARY, summary, 0.0))
        seen: set[str] = set()
        for raw in raw_questions:
            question = str(raw or "").strip()
            normalized = question.casefold()
            if not question or normalized in seen or len(question) > 500:
                continue
            seen.add(normalized)
            rows.append(self._artifact(
                revision, DerivedKind.SYNTHETIC_QUESTION,
                question, self.question_weight,
            ))
        return tuple(rows)

    def citation_for(self, artifact: DerivedArtifact) -> dict[str, str]:
        """Derived hits always cite the immutable source revision, never themselves."""
        return {
            "document_id": artifact.document_id,
            "chunk_id": artifact.chunk_id,
            "revision_id": artifact.revision_id,
        }

    def _artifact(
        self, revision: ChunkRevision, kind: DerivedKind,
        text: str, weight: float,
    ) -> DerivedArtifact:
        digest = hashlib.sha256(
            "\x1f".join((revision.revision_id, kind.value, text, self.model,
                          self.prompt_version)).encode(),
        ).hexdigest()[:24]
        return DerivedArtifact(
            artifact_id=f"derived_{digest}", kind=kind,
            collection=revision.collection, document_id=revision.document_id,
            chunk_id=revision.chunk_id, revision_id=revision.revision_id,
            text=text, model=self.model, prompt_version=self.prompt_version,
            weight=weight, enabled=True, created_at=datetime.now(timezone.utc),
        )


__all__ = ["DerivedContentService"]
