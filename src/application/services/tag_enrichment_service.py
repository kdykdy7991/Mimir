"""Generate reviewable tag suggestions without touching manual bindings."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from uuid import uuid4

from src.application.contracts import SuggestionStatus, TagSuggestion
from src.ingestion.storage import EnrichmentStore


def normalize_tag(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


class TagEnrichmentService:
    def __init__(
        self, store: EnrichmentStore,
        generator: Callable[[str, tuple[str, ...]], Iterable[dict[str, Any]]],
        *, model: str, prompt_version: str, enabled: bool = False,
    ) -> None:
        self.store = store
        self.generator = generator
        self.model = model
        self.prompt_version = prompt_version
        self.enabled = enabled

    def suggest(
        self, *, collection: str, document_id: str, revision_id: str,
        text: str, vocabulary: Iterable[tuple[str, str]],
        manual_tag_ids: Iterable[str] = (),
    ) -> tuple[TagSuggestion, ...]:
        if not self.enabled:
            return ()
        vocab = {normalize_tag(name): (tag_id, name) for tag_id, name in vocabulary}
        manual = set(manual_tag_ids)
        try:
            raw = list(self.generator(text, tuple(name for _, name in vocab.values())))
        except Exception:
            return ()
        out: list[TagSuggestion] = []
        seen: set[str] = set()
        for item in raw:
            name = str(item.get("name") or "").strip()
            normalized = normalize_tag(name)
            if not normalized or normalized in seen or len(name) > 64:
                continue
            seen.add(normalized)
            confidence = item.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                continue
            confidence = float(confidence)
            if not 0 <= confidence <= 1:
                continue
            match = vocab.get(normalized)
            existing_id = match[0] if match else None
            if existing_id in manual:
                continue
            canonical_name = match[1] if match else name
            suggestion = TagSuggestion(
                suggestion_id=f"tagsug_{uuid4().hex}",
                collection=collection, document_id=document_id,
                revision_id=revision_id, suggested_name=canonical_name,
                existing_tag_id=existing_id, model=self.model,
                prompt_version=self.prompt_version, confidence=confidence,
                status=SuggestionStatus.PENDING,
                created_at=datetime.now(timezone.utc),
            )
            out.append(self.store.add(suggestion, normalized))
        return tuple(out)

    def review(
        self, suggestion_id: str, *, approve: bool, reviewer: str,
        resolved_tag_id: str | None = None,
        apply_existing_tag: Callable[[str, str], None] | None = None,
    ) -> TagSuggestion:
        status = SuggestionStatus.APPROVED if approve else SuggestionStatus.REJECTED
        reviewed = self.store.review(
            suggestion_id, status, reviewer, existing_tag_id=resolved_tag_id,
        )
        if approve and apply_existing_tag is not None:
            # Additive callback only: callers must INSERT OR IGNORE and must
            # never full-replace the document's manual tag set.
            apply_existing_tag(reviewed.document_id, str(reviewed.existing_tag_id))
        return reviewed


__all__ = ["TagEnrichmentService", "normalize_tag"]
