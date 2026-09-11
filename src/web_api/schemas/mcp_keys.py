"""DTOs for trusted-admin MCP API-key management endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.web_api.schemas._types import UtcDatetime


class MCPKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    allowed_collections: list[str] = Field(..., min_length=1)


class MCPKeyRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class MCPKeyCollectionsUpdateRequest(BaseModel):
    allowed_collections: list[str] = Field(..., min_length=1)


class MCPKeyMetadata(BaseModel):
    key_id: str
    name: str
    allowed_collections: list[str]
    enabled: bool
    created_at: UtcDatetime
    revoked_at: UtcDatetime | None
    last_used_at: UtcDatetime | None


class MCPKeySecretResponse(MCPKeyMetadata):
    """Returned only by create/rotate; ``api_key`` is never listable."""

    api_key: str = Field(
        ...,
        description=(
            "The MCP Client Key. Returned exactly once at create/rotate time "
            "and never readable again; send-only for the connection test."
        ),
        json_schema_extra={"writeOnly": True, "format": "password"},
    )


class MCPKeyListResponse(BaseModel):
    items: list[MCPKeyMetadata]


__all__ = [
    "MCPKeyCollectionsUpdateRequest", "MCPKeyCreateRequest", "MCPKeyRenameRequest",
    "MCPKeyListResponse", "MCPKeyMetadata", "MCPKeySecretResponse",
]
