"""Collection-scoped governance filter resolution for unified search."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.application.contracts import EvidenceFilterV1, TagOperator
from src.application.identifiers import collection_uuid, document_uuid


@dataclass(frozen=True)
class ResolvedGovernanceFilter:
    """Resolved storage constraint plus final defensive candidate filters."""

    source_paths: frozenset[str] | None = None
    content_types: frozenset[str] | None = None
    source_types: frozenset[str] | None = None

    def metadata_filter(self) -> dict[str, Any] | None:
        """Dense-store prefilter when one exact source remains."""
        if self.source_paths is not None and len(self.source_paths) == 1:
            return {"source_path": next(iter(self.source_paths))}
        return None

    def matches(self, candidate: Any) -> bool:
        metadata = getattr(candidate, "metadata", None) or {}
        if self.source_paths is not None:
            if str(metadata.get("source_path") or "") not in self.source_paths:
                return False
        if self.content_types is not None:
            if str(metadata.get("content_type") or "text") not in self.content_types:
                return False
        if self.source_types is not None:
            source_type = str(
                metadata.get("source_type")
                or getattr(candidate, "source", "")
                or ""
            )
            if source_type not in self.source_types:
                return False
        return True


class GovernanceFilterResolver:
    """Resolve public filter ids without widening the selected collection."""

    def __init__(self, document_service: Any, db: Any | None = None) -> None:
        self._documents = document_service
        self._db = db

    def resolve(
        self, collection: str, filters: EvidenceFilterV1 | None,
    ) -> ResolvedGovernanceFilter:
        if filters is None:
            return ResolvedGovernanceFilter()
        if filters.collection_ids is not None and collection not in filters.collection_ids:
            return ResolvedGovernanceFilter(source_paths=frozenset())

        key_rows = list(self._documents.list_document_keys(collection=collection))
        id_to_path = {
            str(document_uuid(coll, path)): path for coll, path in key_rows
            if coll == collection
        }
        allowed: set[str] | None = None

        if filters.document_ids is not None:
            allowed = {
                id_to_path[doc_id] for doc_id in filters.document_ids
                if doc_id in id_to_path
            }

        collection_id = str(collection_uuid(collection))
        if filters.tag_ids is not None:
            tagged_ids: set[str] = set()
            if self._db is not None:
                if filters.tag_operator is TagOperator.AND:
                    tagged_ids = set(
                        self._db.documents_with_all_tags(list(filters.tag_ids)),
                    )
                else:
                    tag_map = self._db.document_tags_map(list(id_to_path))
                    wanted = set(filters.tag_ids)
                    tagged_ids = {
                        doc_id for doc_id, tags in tag_map.items()
                        if wanted.intersection(tags)
                    }
            tagged_paths = {id_to_path[d] for d in tagged_ids if d in id_to_path}
            allowed = tagged_paths if allowed is None else allowed & tagged_paths

        if filters.folder_id is not None:
            folder_ids: list[str | None]
            if filters.folder_id == "root":
                folder_ids = [None]
            else:
                folder_ids = [filters.folder_id]
                if filters.include_descendants and self._db is not None:
                    folder_ids.extend(self._db.descendant_folder_ids(filters.folder_id))
            folder_docs: set[str] = set()
            if self._db is not None:
                for folder_id in folder_ids:
                    folder_docs.update(
                        self._db.documents_by_folder(folder_id, collection_id),
                    )
            folder_paths = {id_to_path[d] for d in folder_docs if d in id_to_path}
            allowed = folder_paths if allowed is None else allowed & folder_paths

        if (
            filters.file_types is not None
            or filters.updated_after is not None
            or filters.updated_before is not None
        ):
            file_types = {
                value.lower().lstrip(".") for value in (filters.file_types or ())
            }
            infos = self._documents.list_documents(collection=collection)
            governed = {
                info.source_path for info in infos
                if (not file_types or Path(info.source_path).suffix.lower().lstrip(".") in file_types)
                and (
                    filters.updated_after is None
                    or (info.updated_at or info.last_modified or 0)
                    >= filters.updated_after.timestamp()
                )
                and (
                    filters.updated_before is None
                    or (info.updated_at or info.last_modified or 0)
                    <= filters.updated_before.timestamp()
                )
            }
            allowed = governed if allowed is None else allowed & governed

        return ResolvedGovernanceFilter(
            source_paths=frozenset(allowed) if allowed is not None else None,
            content_types=(frozenset(filters.content_types) if filters.content_types else None),
            source_types=(frozenset(filters.source_types) if filters.source_types else None),
        )


__all__ = ["GovernanceFilterResolver", "ResolvedGovernanceFilter"]
