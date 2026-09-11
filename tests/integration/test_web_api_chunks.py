"""
B1.1 — ``GET /api/v1/documents/{document_id}/chunks/{chunk_id}``.

Coverage required by the task book: normal read, first/last chunk neighbors,
legacy (old) data ordering, an id that does not belong to the document,
anti-enumeration for an unknown document, and missing locator info degrading
to ``none``/``null``.

Uses a stub document service (no disk/Chroma/LLM) following the existing
web-API integration-test pattern.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.application.composition import ApplicationServices
from src.web_api.app import create_app

COLLECTION = "kb"
SOURCE_PATH = "/data/uploads/kb/intern-handbook.pdf"
DOC_ID = "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f"


def _chunk(chunk_id: str, text: str, chunk_index: int | None = None, **meta) -> dict:
    m = dict(meta)
    if chunk_index is not None:
        m["chunk_index"] = chunk_index
    return {"id": chunk_id, "text": text, "metadata": m}


class _StubDocument:
    """Minimal ``document`` service producing controlled chunk sets."""

    def __init__(self, chunks, *, resolve=True):
        self._chunks = list(chunks)
        self._resolve = resolve

    def resolve_document_id(self, document_id):
        if self._resolve:
            return (COLLECTION, SOURCE_PATH)
        return None

    def get_document_detail(self, source_path, collection):
        if not self._resolve or source_path != SOURCE_PATH or collection != COLLECTION:
            return None
        detail = type("D", (), {})
        detail.chunks = self._chunks
        detail.info = None
        return detail


def _client(doc: _StubDocument) -> TestClient:
    services = ApplicationServices(
        query=object(), ingestion=object(), document=doc,
        system=object(), trace=object(), engines=object(),
    )
    return TestClient(create_app(services=services))


def _pdf_doc() -> _StubDocument:
    chunks = [
        _chunk("c0", "# 1 安装\n正文零", chunk_index=0, page=1),
        _chunk("c1", "3.2 服务部署\n正文一", chunk_index=1, page=12,
               section="3.2 服务部署"),
        _chunk("c2", "4 附录\n正文二", chunk_index=2, page=20),
    ]
    return _StubDocument(chunks)


def test_normal_single_chunk_detail() -> None:
    client = _client(_pdf_doc())
    resp = client.get(f"/api/v1/documents/{DOC_ID}/chunks/c1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chunk_id"] == "c1"
    assert body["document_id"] == DOC_ID
    assert body["index"] == 1
    assert body["text"] == "3.2 服务部署\n正文一"
    assert body["heading"] == "3.2 服务部署"
    assert body["page"] == 12
    assert body["content_type"] == "text"
    assert body["character_count"] == len("3.2 服务部署\n正文一")
    assert body["previous_chunk_id"] == "c0"
    assert body["next_chunk_id"] == "c2"
    assert body["source_locator"] == {"kind": "pdf_page", "page": 12}


def test_first_and_last_chunk_neighbors() -> None:
    client = _client(_pdf_doc())
    first = client.get(f"/api/v1/documents/{DOC_ID}/chunks/c0").json()
    assert first["index"] == 0
    assert first["previous_chunk_id"] is None
    assert first["next_chunk_id"] == "c1"

    last = client.get(f"/api/v1/documents/{DOC_ID}/chunks/c2").json()
    assert last["index"] == 2
    assert last["previous_chunk_id"] == "c1"
    assert last["next_chunk_id"] is None


def test_legacy_data_without_chunk_index() -> None:
    """Old vector rows that only embed the index in the id still order."""
    chunks = [
        _chunk("doc_0001_chunk", "旧一", page=1),
        _chunk("doc_0002_chunk", "旧二", page=2),
        _chunk("doc_0003_chunk", "旧三", page=3),
    ]
    client = _client(_StubDocument(chunks))
    mid = client.get(f"/api/v1/documents/{DOC_ID}/chunks/doc_0002_chunk")
    assert mid.status_code == 200, mid.text
    assert mid.json()["index"] == 1
    assert mid.json()["previous_chunk_id"] == "doc_0001_chunk"
    assert mid.json()["next_chunk_id"] == "doc_0003_chunk"


def test_chunk_id_from_wrong_document_is_not_found() -> None:
    client = _client(_pdf_doc())
    resp = client.get(f"/api/v1/documents/{DOC_ID}/chunks/does-not-belong")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CHUNK_NOT_FOUND"


def test_unknown_document_is_not_found() -> None:
    client = _client(_StubDocument([_chunk("a", "x", chunk_index=0)], resolve=False))
    resp = client.get("/api/v1/documents/ffffffff-0000-0000-0000-000000000000/chunks/a")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_missing_locator_info_degrades_gracefully() -> None:
    """No page/heading -> ``source_locator.kind == 'none'``, page null."""
    chunks = [_chunk("noinfo", "plain chunk without locator", chunk_index=0)]
    client = _client(_StubDocument(chunks))
    resp = client.get(f"/api/v1/documents/{DOC_ID}/chunks/noinfo")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page"] is None
    assert body["heading"] is None
    assert body["source_locator"]["kind"] == "none"
    assert body["source_locator"]["page"] is None



def test_locator_kinds_image_section_none() -> None:
    """Locator kind follows content_type/file-extension (B1.3 mapping)."""
    # image content type -> image kind, no page
    img = _client_for_source(".png", [
        _chunk("img", "OCR text", chunk_index=0, content_type="image_ocr", page=3),
    ]).get(f"/api/v1/documents/{DOC_ID}/chunks/img").json()
    assert img["source_locator"]["kind"] == "image"
    assert img["source_locator"]["page"] is None

    # markdown with a heading -> section kind
    sec = _client_for_source(".md", [
        _chunk("md", "## 目标\nMarkdown body", chunk_index=0, page=2),
    ]).get(f"/api/v1/documents/{DOC_ID}/chunks/md").json()
    assert sec["source_locator"]["kind"] == "section"
    assert sec["source_locator"]["page"] is None

    # pdf without a page -> none kind
    none = _client_for_source(".pdf", [
        _chunk("np", "no page", chunk_index=0),
    ]).get(f"/api/v1/documents/{DOC_ID}/chunks/np").json()
    assert none["source_locator"]["kind"] == "none"
    assert none["source_locator"]["page"] is None


def _client_for_source(ext: str, chunks, *, resolve_source: str | None = None) -> TestClient:
    """Build a client whose resolved source path ends with ``ext`` (locator input)."""

    class _SourceDoc:
        def resolve_document_id(self, document_id):
            return (COLLECTION, resolve_source or f"{SOURCE_PATH[:-4]}{ext}")

        def get_document_detail(self, source_path, collection):
            d = type("D", (), {})
            d.chunks = chunks
            d.info = None
            return d

    services = ApplicationServices(
        query=object(), ingestion=object(), document=_SourceDoc(),
        system=object(), trace=object(), engines=object(),
    )
    return TestClient(create_app(services=services))
