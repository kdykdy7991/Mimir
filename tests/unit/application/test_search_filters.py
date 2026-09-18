from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from src.application.contracts import EvidenceFilterV1
from src.application.identifiers import document_uuid
from src.application.services.search_filters import GovernanceFilterResolver


class Documents:
    keys = [("kb", "/a.pdf"), ("kb", "/b.md"), ("kb", "/c.pdf")]

    def list_document_keys(self, collection=None):
        return self.keys

    def list_documents(self, collection=None):
        return [
            SimpleNamespace(source_path="/a.pdf", updated_at=20, last_modified=20),
            SimpleNamespace(source_path="/b.md", updated_at=30, last_modified=30),
            SimpleNamespace(source_path="/c.pdf", updated_at=40, last_modified=40),
        ]


class DB:
    def documents_with_all_tags(self, tags):
        return [str(document_uuid("kb", "/a.pdf"))]

    def document_tags_map(self, ids):
        return {
            str(document_uuid("kb", "/a.pdf")): ["t1"],
            str(document_uuid("kb", "/b.md")): ["t2"],
        }

    def documents_by_folder(self, folder_id, collection_id):
        return {
            "parent": [str(document_uuid("kb", "/a.pdf"))],
            "child": [str(document_uuid("kb", "/c.pdf"))],
            None: [str(document_uuid("kb", "/b.md"))],
        }.get(folder_id, [])

    def descendant_folder_ids(self, folder_id):
        return ["child"] if folder_id == "parent" else []


def test_document_tag_and_folder_filters_intersect():
    doc_id = str(document_uuid("kb", "/a.pdf"))
    result = GovernanceFilterResolver(Documents(), DB()).resolve(
        "kb", EvidenceFilterV1(
            document_ids=[doc_id], tag_ids=["t1"], folder_id="parent",
        ),
    )
    assert result.source_paths == frozenset({"/a.pdf"})
    assert result.metadata_filter() == {"source_path": "/a.pdf"}


def test_descendants_root_and_or_tags():
    resolver = GovernanceFilterResolver(Documents(), DB())
    descendants = resolver.resolve(
        "kb", EvidenceFilterV1(folder_id="parent", include_descendants=True),
    )
    assert descendants.source_paths == frozenset({"/a.pdf", "/c.pdf"})
    root = resolver.resolve("kb", EvidenceFilterV1(folder_id="root"))
    assert root.source_paths == frozenset({"/b.md"})
    tagged = resolver.resolve(
        "kb", EvidenceFilterV1(tag_ids=["t2"], tag_operator="or"),
    )
    assert tagged.source_paths == frozenset({"/b.md"})


def test_file_time_and_candidate_filters():
    utc = dt.timezone.utc
    result = GovernanceFilterResolver(Documents(), DB()).resolve(
        "kb", EvidenceFilterV1(
            file_types=["pdf"],
            updated_after=dt.datetime.fromtimestamp(25, utc),
            content_types=["table"], source_types=["dense"],
        ),
    )
    assert result.source_paths == frozenset({"/c.pdf"})
    candidate = SimpleNamespace(
        metadata={"source_path": "/c.pdf", "content_type": "table"},
        source="dense",
    )
    assert result.matches(candidate)
    candidate.source = "sparse"
    assert not result.matches(candidate)


def test_foreign_collection_filter_is_empty_not_leaky():
    result = GovernanceFilterResolver(Documents(), DB()).resolve(
        "kb", EvidenceFilterV1(collection_ids=["other"]),
    )
    assert result.source_paths == frozenset()
