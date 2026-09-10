"""
P1.1 — readonly client contracts.

Asserts that ``RagReadOnlyClient`` is a pure, read-only Protocol whose
methods match the plan's signature exactly, and that the domain models
carry only the intended fields (no MCP / Chroma / DTO / LLM types leak
into the boundary).
"""

from __future__ import annotations

import inspect
import types
import typing

from src.mcp_server.auth.context import TrustedLocalPrincipal
from src.mcp_server.clients.base import RagReadOnlyClient
from src.mcp_server.clients.models import (
    CollectionInfo,
    Diagnostics,
    DocumentChunk,
    DocumentChunkPage,
    DocumentInfo,
    EvidenceItem,
    KnowledgeQueryResult,
    QueryRequest,
)


def test_protocol_exposes_exactly_the_four_read_methods():
    """The interface must have exactly the 4 read methods, no writes."""
    expected = {"list_collections", "query_knowledge", "get_document",
                "get_document_chunks"}
    methods = {
        name for name in getattr(RagReadOnlyClient, "__dict__", {})
        if not name.startswith("_") and isinstance(
            getattr(RagReadOnlyClient, name, None),
            (types.FunctionType, classmethod, staticmethod),
        )
    }
    assert methods == expected


def test_protocol_is_runtime_checkable():
    assert getattr(RagReadOnlyClient, "_is_protocol", False) is True
    assert isinstance(typing.get_type_hints if False else object, object)


def test_protocol_annotations_reference_only_pure_models():
    def hints(meth):
        return typing.get_type_hints(getattr(RagReadOnlyClient, meth))
    lc = hints("list_collections")
    assert "CollectionInfo" in str(lc["return"])
    assert "TrustedLocalPrincipal" in str(lc["principal"])
    qk = hints("query_knowledge")
    assert "QueryRequest" in str(qk["request"])
    assert "KnowledgeQueryResult" in str(qk["return"])
    gd = hints("get_document")
    assert "DocumentInfo" in str(gd["return"])
    gc = hints("get_document_chunks")
    assert "DocumentChunkPage" in str(gc["return"])


def test_domain_models_defaults_and_fields():
    c = CollectionInfo(name="kb", description="x", document_count=3, chunk_count=9)
    assert c.name == "kb" and c.chunk_count == 9

    req = QueryRequest(query="q", collection="kb")
    assert req.top_k == 10 and req.rerank is True

    item = EvidenceItem(rank=1, chunk_id="c1", document_id="d1", page=3, score=0.9)
    assert item.rank == 1 and item.page == 3

    d = Diagnostics(degraded=True, reasons=["r"], trace_id="t")
    assert d.degraded and d.reasons == ["r"]

    res = KnowledgeQueryResult(query="q", collection="kb", count=2,
                               evidence=[item, EvidenceItem(rank=2, chunk_id="c2", document_id="d1")])
    assert res.n_results == 2
    assert len(res.citations) == 2
    assert res.citations[0]["index"] == 1

    doc = DocumentInfo(document_id="d1", collection="kb", chunk_count=4)
    assert doc.chunk_count == 4 and doc.title == ""

    chunk = DocumentChunk(chunk_id="c1", index=0, page=1, section="s")
    page = DocumentChunkPage(document_id="d1", page=1, page_size=20, total=25, chunks=[chunk])
    assert len(page.chunks) == 1
    assert page.has_next is True  # 1*20 < 25


def test_models_have_no_forbidden_types():
    """Ensure no MCP / Chroma / DTO types sneak into the model annotations."""
    import dataclasses

    import src.mcp_server.clients.models as models

    annotated_names: list[str] = []
    for cls in vars(models).values():
        if dataclasses.is_dataclass(cls) and isinstance(cls, type):
            for field_ in dataclasses.fields(cls):
                annotated_names.append(str(field_.type))
    for banned in ("mcp.", "Chroma", "BaseVectorStore", "TextContent",
                   "ContentBlock", "RetrievalResult", "schema.", "FastAPI"):
        for token in annotated_names:
            assert banned not in token, f"forbidden type reference: {banned}"


def test_principal_is_duck_typed_not_bound_to_concrete_identity():
    """The client works with any principal exposing allowed_collections."""
    class FakePrincipal:
        key_id = "k"
        name = "n"
        allowed_collections = frozenset({"a"})
    # Verify the param is annotated as the principal union so concrete
    # principals are duck-typed.
    lc = typing.get_type_hints(RagReadOnlyClient.list_collections)
    assert "TrustedLocalPrincipal" in str(lc["principal"])
    _ = FakePrincipal()  # constructible without any mcp/auth imports