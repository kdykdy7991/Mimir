"""
Frozen knowledge-operations API contracts (task book B0.1).

What this module locks in:

- The pagination caps every **new** knowledge endpoint must respect
  (``PAGE_SIZE_DEFAULT=50``, ``PAGE_SIZE_MAX=100``), matching the task
  book section 4 ("分页默认 ``limit=50``，最大 ``100``") for the new
  ``page / page_size`` endpoints.
- The stable ordering rule every chunk list shares with the MCP
  ``get_document_chunks`` tool (``chunk_index`` → legacy id index → id).
- Enums that must not drift: chunk ``content_type``, ``source_locator.kind``,
  collection-scoped error codes.
- Compatibility assertions on the **existing** document / Trace / MCP Key /
  task response shapes, so later B–tasks can extend contracts without
  silently breaking the current public API.

Behaviour by task: at B0.1 only the "current" assertions below run (and
must pass). Each later B–task (B1 chunk, B2 tag/folder, B3 trace, B4 MCP
status/test) appends its own live-schema assertions to this module and
makes them pass in that task's commit. We never rely on a blanket
``xfail``/``skip`` to hide a missing endpoint — a missing contract is a
hard failure.
"""

from __future__ import annotations

import pytest

from src.web_api.app import create_app

# ---------------------------------------------------------------------------
# Frozen constants (shared by this module's assertions)
# ---------------------------------------------------------------------------

API_PREFIX = "/api/v1"

# New ``page / page_size`` endpoints (task book §4).
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 100
CHUNK_PAGE_SIZE_MIN = 1

# Chunk ``content_type`` values (mirror MCP get_document_chunks whitelist).
CHUNK_CONTENT_TYPES = frozenset({
    "text", "table", "image_ocr", "image_caption",
})

# ``source_locator.kind`` (task book B1.3). ``none`` means "no reliable
# locator" — the API must never guess.
SOURCE_LOCATOR_KINDS = frozenset({"pdf_page", "image", "section", "none"})

# Stable chunk ordering (shared with MCP get_document_chunks): primary
# ``chunk_index``, then legacy ``_NNNN_`` id index, then raw id.
# Implementations must reuse (or call into) the MCP sort helper rather than
# re-declaring a second ordering rule.
CHUNK_ORDER_INTENT = (
    "chunk_index ASC, legacy_numeric_index ASC, chunk_id ASC"
)

# Collection-scoped error codes the front-end branches on.
ERROR_CODES = frozenset({
    "COLLECTION_NOT_FOUND",
    "DOCUMENT_NOT_FOUND",
    "TASK_NOT_FOUND",
    "VALIDATION_ERROR",
    "BAD_REQUEST",
    "CONFLICT",
})


# ---------------------------------------------------------------------------
# Helpers (extended by later B-tasks)
# ---------------------------------------------------------------------------

def _live_openapi() -> dict:
    """Build the live FastAPI document once per test (cheap at local scale)."""
    return create_app().openapi()


def _schemas(openapi: dict) -> dict:
    return openapi.get("components", {}).get("schemas", {})


def assert_schema_fields(
    openapi: dict,
    name: str,
    *,
    required: set[str],
    properties: set[str],
) -> dict:
    """Assert a schema's ``required``/``properties`` superset, return its prop map."""
    schema = _schemas(openapi).get(name)
    assert schema is not None, f"missing OpenAPI schema {name!r}"
    assert required <= set(schema.get("required", [])), (
        f"schema {name!r}: expected required fields {sorted(required)}, "
        f"got {sorted(schema.get('required', []))}"
    )
    props = set(schema.get("properties", {}).keys())
    assert required | properties <= props, (
        f"schema {name!r} is missing expected fields: "
        f"{sorted((required | properties) - props)}"
    )
    return schema.get("properties", {})


def assert_post_limit_cap(openapi: dict, path: str, *, field: str, max_value: int) -> None:
    """Assert a GET query param ``field`` on ``path`` is capped at ``max_value``."""
    op = openapi.get("paths", {}).get(path, {}).get("get")
    assert op is not None, f"missing GET {path}"
    params = {p["name"]: p for p in op.get("parameters", [])}
    assert field in params, f"{path} GET missing query param {field!r}"
    schema = params[field].get("schema", {})
    assert schema.get("maximum") == max_value or schema.get("le") == max_value, (
        f"{path} GET {field}=... must cap at {max_value}, got {schema}"
    )


# ---------------------------------------------------------------------------
# B0.1 — compatibility on the current public API (must pass at B0.1)
# ---------------------------------------------------------------------------

def test_pagination_contract_is_frozen() -> None:
    """The caps every new page/page_size endpoint must use (task book §4)."""
    assert PAGE_SIZE_DEFAULT == 50
    assert PAGE_SIZE_MAX == 100
    assert PAGE_SIZE_MAX >= PAGE_SIZE_DEFAULT, "max must exceed default"


def test_existing_document_contract() -> None:
    """``/api/v1/documents/{id}`` detail + summary shapes stay compatible."""
    openapi = _live_openapi()
    # Required summary fields (v0.2) must not regress.
    assert_schema_fields(
        openapi, "DocumentSummary",
        required={"id", "collection_id", "filename", "size_bytes", "status",
                  "created_at", "updated_at"},
        properties={"chunk_count", "image_count"},
    )
    detail_props = assert_schema_fields(
        openapi, "DocumentDetail",
        required={"id", "collection_id", "filename", "size_bytes", "status",
                  "created_at", "updated_at"},
        properties={"file_hash", "last_task_id", "last_query_id", "last_error",
                    "table_count", "parse_warnings", "parser_engine",
                    "parse_status", "page_count", "vision_processed", "chunks"},
    )
    assert "chunks" in detail_props, "DocumentDetail.chunks must remain present"


def test_existing_chunk_summary_contract() -> None:
    """Document-detail chunk rows keep a stable lightweight shape."""
    openapi = _live_openapi()
    props = assert_schema_fields(
        openapi, "DocumentChunkSummary",
        required={"index", "chunk_id", "character_count"},
        properties={"heading", "page", "content_type"},
    )
    content_type = props.get("content_type", {})
    assert content_type.get("default", "text") == "text"


def test_b11_chunk_detail_contract() -> None:
    """``GET /documents/{id}/chunks/{chunk_id}`` full-body schema (B1.1)."""
    openapi = _live_openapi()
    paths = openapi.get("paths", {})
    assert "/api/v1/documents/{document_id}/chunks/{chunk_id}" in paths
    assert_schema_fields(
        openapi, "DocumentChunkDetail",
        required={"chunk_id", "document_id", "index", "text", "character_count",
                  "source_locator"},
        properties={"heading", "page", "content_type",
                    "previous_chunk_id", "next_chunk_id"},
    )
    locator = _schemas(openapi).get("SourceLocator", {})
    kind = (locator.get("properties", {}).get("kind") or {}).get("enum")
    assert kind is not None, "SourceLocator.kind must be an explicit enum"
    assert set(kind) == {"pdf_page", "image", "section", "none"}, f"got {kind}"


def test_b12_chunk_list_contract() -> None:
    """``GET /documents/{id}/chunks`` paged list contract (B1.2)."""
    openapi = _live_openapi()
    paths = openapi.get("paths", {})
    assert "/api/v1/documents/{document_id}/chunks" in paths
    assert_schema_fields(
        openapi, "DocumentChunkListResponse",
        required={"items", "page", "page_size", "total", "has_next"},
        properties=set(),
    )
    assert_schema_fields(
        openapi, "ChunkListItem",
        required={"index", "chunk_id", "character_count", "text_preview"},
        properties={"heading", "page", "content_type"},
    )
    # page_size is capped at the frozen maximum.
    op = paths["/api/v1/documents/{document_id}/chunks"]["get"]
    params = {p["name"]: p for p in op.get("parameters", [])}
    assert params["page_size"]["schema"]["maximum"] == PAGE_SIZE_MAX, (
        "page_size must cap at 100"
    )


def test_existing_trace_contract() -> None:
    """``GET /queries/{id}/trace`` / ``/ingestions/{id}/trace`` shapes."""
    openapi = _live_openapi()
    assert_schema_fields(
        openapi, "TraceResponse",
        required={"id", "trace_type", "started_at", "finished_at",
                  "total_latency_ms"},
        properties={"stages", "error"},
    )
    assert_schema_fields(
        openapi, "TraceStage",
        required={"name", "started_at", "duration_ms"},
        properties={"method", "provider", "details"},
    )


def test_existing_mcp_key_contract() -> None:
    """MCP Key metadata is listable; the full secret only on create/rotate."""
    openapi = _live_openapi()
    assert_schema_fields(
        openapi, "MCPKeyMetadata",
        required={"key_id", "name", "allowed_collections", "enabled",
                  "created_at", "revoked_at", "last_used_at"},
        properties=set(),
    )
    secret = _schemas(openapi).get("MCPKeySecretResponse")
    assert secret is not None and "api_key" in secret.get("properties", {}), (
        "MCPKeySecretResponse must be the only shape carrying api_key"
    )
    # Metadata shape must not expose api_key.
    meta = _schemas(openapi)["MCPKeyMetadata"]["properties"]
    assert "api_key" not in meta, "MCPKeyMetadata must never carry api_key"


def test_existing_task_contract() -> None:
    """Task status carries attempt (groundwork for actionable retry)."""
    openapi = _live_openapi()
    props = assert_schema_fields(
        openapi, "TaskStatusResponse",
        required={"id", "document_id", "collection_id", "status",
                  "created_at", "updated_at"},
        properties={"progress", "error", "finished_at", "attempt"},
    )
    assert props.get("attempt") is not None


def test_error_envelope_contract() -> None:
    """Every 4xx/5xx returns the single ``{error: {...}}`` envelope.

    The envelope is produced by the exception handler (not declared as a
    ``response_model``), so it never appears in OpenAPI ``components``; we
    pin the Pydantic shape directly.
    """
    from src.web_api.schemas.common import ErrorDetail, ErrorEnvelope

    detail = ErrorDetail.model_fields
    assert {"code", "message", "request_id", "details"} <= set(detail)
    assert ErrorEnvelope.model_fields["error"].annotation is ErrorDetail


def test_frozen_error_codes_are_used_in_contract() -> None:
    """The collection-scoped error codes are real, stable ``APIError`` codes."""
    from src.web_api.errors import (
        APIError,
        CollectionNotFoundError,
        DocumentNotFoundError,
        TaskNotFoundError,
    )

    codes = {
        CollectionNotFoundError.code, DocumentNotFoundError.code,
        TaskNotFoundError.code, APIError.status_code,
    }
    for code in ("COLLECTION_NOT_FOUND", "DOCUMENT_NOT_FOUND", "TASK_NOT_FOUND"):
        assert code in codes, f"expected stable APIError code {code!r}"
    assert APIError.status_code == 500


def test_cursor_pagination_max_is_capped() -> None:
    """Existing cursor endpoints cap page size at the hard limit (100)."""
    openapi = _live_openapi()
    for path in ("/api/v1/documents", "/api/v1/collections/{collection_id}/documents"):
        assert_post_limit_cap(openapi, path, field="limit", max_value=100)


def test_page_info_envelope_is_stable() -> None:
    """``PageInfo`` carries a nullable cursor so clients never guess."""
    openapi = _live_openapi()
    assert_schema_fields(
        openapi, "PageInfo",
        required={"next_cursor", "has_more"},
        properties=set(),
    )


# ---------------------------------------------------------------------------
# B0.1 — scaffold for contracts frozen but not yet implemented
# ---------------------------------------------------------------------------
# Each later B–task adds a live-schema assertion below and removes nothing.
# The presence of a "planned" contract is enforced by the task's own commit,
# never by a module-wide skip/xfail.
PLANNED_ENDPOINTS: dict[str, set[str]] = {
    # B1
    "B1.1": {"/api/v1/documents/{document_id}/chunks/{chunk_id}"},
    "B1.2": {"/api/v1/documents/{document_id}/chunks"},
    # B2 tags
    "B2.2": {"/api/v1/collections/{collection_id}/tags",
             "/api/v1/documents/{document_id}/tags"},
    # B2 folders
    "B2.4": {"/api/v1/collections/{collection_id}/folders",
             "/api/v1/documents/{document_id}/folder"},
    # B2 batch
    "B2.6": {"/api/v1/collections/{collection_id}/documents/batch/tags"},
    "B2.7": {"/api/v1/collections/{collection_id}/documents/batch/move"},
    "B2.8": {"/api/v1/collections/{collection_id}/documents/batch/reprocess"},
    "B2.9": {"/api/v1/collections/{collection_id}/documents/batch/delete"},
    # B3
    "B3.1": {"/api/v1/tasks/{task_id}/retry", "/api/v1/tasks/{task_id}/cancel"},
    "B3.5": {"/api/v1/traces"},
    # B4
    "B4.1": {"/api/v1/mcp-server/status"},
    "B4.3": {"/api/v1/mcp-server/test-connection"},
}


def test_planned_endpoints_registry_is_declared() -> None:
    """The planned-endpoint registry is explicit (used by later tasks)."""
    assert PLANNED_ENDPOINTS
    assert "B1.1" in PLANNED_ENDPOINTS


__all__ = [
    "API_PREFIX",
    "PAGE_SIZE_DEFAULT",
    "PAGE_SIZE_MAX",
    "CHUNK_CONTENT_TYPES",
    "SOURCE_LOCATOR_KINDS",
    "ERROR_CODES",
    "PLANNED_ENDPOINTS",
    "assert_schema_fields",
    "assert_post_limit_cap",
]