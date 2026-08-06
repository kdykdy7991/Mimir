"""
Common API DTOs shared across routers.

Defines the project-wide error envelope, cursor pagination envelope,
and request_id propagation type. All routers depend on these.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """Inner ``error`` object — stable machine-readable code + human message.

    The ``code`` is the contract; the ``message`` is for humans.
    Frontend MUST NOT branch on ``message`` — see TEAM_RESPONSIBILITIES §4.1.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "DOCUMENT_NOT_FOUND",
                "message": "Document does not exist",
                "request_id": "19fb613e329-298ecef1",
                "details": {"document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f"},
            }
        }
    )

    code: str = Field(
        ...,
        description=(
            "Stable machine-readable error code (e.g. DOCUMENT_NOT_FOUND). "
            "Frontend branches on this, never on `message`."
        ),
        examples=["DOCUMENT_NOT_FOUND"],
    )
    message: str = Field(..., description="Human-readable error description.")
    request_id: str | None = Field(
        None,
        description=(
            "Echoes the X-Request-ID assigned to the request. "
            "Useful when reporting an issue."
        ),
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured context (e.g. field-level errors).",
    )


class ErrorEnvelope(BaseModel):
    """Top-level error response shape.

    Returned with the appropriate HTTP status code for any 4xx/5xx.
    The body ALWAYS has this single ``error`` key — never both ``data`` and
    ``error`` — so the frontend can switch on ``response.error !== undefined``.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": "DOCUMENT_NOT_FOUND",
                        "message": "Document does not exist",
                        "request_id": "19fb613e329-298ecef1",
                        "details": {"document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f"},
                    }
                },
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Request validation failed.",
                        "request_id": "19fb613e32a-a773b11c",
                        "details": {
                            "errors": [
                                {"type": "missing", "loc": ["body", "name"], "msg": "Field required"}
                            ]
                        },
                    }
                },
            ]
        }
    )

    error: ErrorDetail


class PageInfo(BaseModel):
    """Cursor pagination metadata.

    The cursor is opaque to clients; they should pass back whatever
    ``next_cursor`` they got in the previous response.

    ``next_cursor`` is required + nullable: every response carries the
    field, with ``null`` meaning "no more pages". This avoids the
    front-end having to distinguish between "field missing" and
    "field is null".
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "next_cursor": "eyJwYWdlIjoyfQ==",
                "has_more": True,
            }
        }
    )

    next_cursor: str | None = Field(
        ...,
        description="Opaque cursor for the next page. ``null`` means no more pages.",
    )
    has_more: bool = Field(
        ...,
        description="Convenience flag; equivalent to ``next_cursor is not None``.",
    )


class Page(BaseModel, Generic[T]):
    """Cursor-paginated list envelope.

    Generic over the element type. Endpoints use ``Page[CollectionSummary]``
    etc. to get per-endpoint typed lists.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [],
                    "page_info": {"next_cursor": None, "has_more": False},
                },
                {
                    "items": ["<one element>"],
                    "page_info": {"next_cursor": "eyJwYWdlIjoxfQ==", "has_more": True},
                },
            ]
        }
    )

    items: list[T] = Field(..., description="Page elements.")
    page_info: PageInfo = Field(..., description="Pagination cursor info.")


__all__ = [
    "ErrorDetail",
    "ErrorEnvelope",
    "Page",
    "PageInfo",
]
