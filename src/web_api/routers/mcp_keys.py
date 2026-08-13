"""Trusted-admin HTTP API for MCP API-key management.

Authentication for this management surface is intentionally deferred by the
product roadmap. Deploy it only behind the same trusted admin boundary as the
Web UI; no endpoint returns an existing secret or digest.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from src.core.settings import load_settings
from src.mcp_server.auth import ApiKeyService
from src.mcp_server.auth.models import (
    DuplicateKeyNameError, InvalidCollectionWhitelistError, KeyNotFoundError,
)
from src.web_api.errors import BadRequestError, ConflictError, NotFoundError
from src.web_api.schemas.mcp_keys import (
    MCPKeyCreateRequest, MCPKeyListResponse, MCPKeyMetadata, MCPKeySecretResponse,
)

router = APIRouter(prefix="/mcp-keys", tags=["mcp-keys"])


def _service() -> ApiKeyService:
    settings = load_settings("./config/settings.yaml")
    return ApiKeyService(db_path=settings.mcp_access.database_path)


def _metadata(value) -> MCPKeyMetadata:
    return MCPKeyMetadata(
        key_id=value.key_id, name=value.name,
        allowed_collections=sorted(value.allowed_collections), enabled=value.enabled,
        created_at=value.created_at, revoked_at=value.revoked_at,
        last_used_at=value.last_used_at,
    )


@router.get("", response_model=MCPKeyListResponse, summary="List MCP API keys")
async def list_mcp_keys() -> MCPKeyListResponse:
    return MCPKeyListResponse(items=[_metadata(item) for item in _service().list_keys()])


@router.post("", response_model=MCPKeySecretResponse, status_code=status.HTTP_201_CREATED,
             summary="Create an MCP API key")
async def create_mcp_key(body: MCPKeyCreateRequest) -> MCPKeySecretResponse:
    try:
        raw_key, item = _service().create_key(
            name=body.name, allowed_collections=set(body.allowed_collections),
        )
    except DuplicateKeyNameError as exc:
        raise ConflictError(str(exc)) from exc
    except (InvalidCollectionWhitelistError, ValueError) as exc:
        raise BadRequestError(str(exc)) from exc
    return MCPKeySecretResponse(**_metadata(item).model_dump(), api_key=raw_key)


@router.post("/{name}/rotate", response_model=MCPKeySecretResponse,
             summary="Rotate an MCP API key")
async def rotate_mcp_key(name: str) -> MCPKeySecretResponse:
    try:
        raw_key, item = _service().rotate_key(name=name)
    except KeyNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    return MCPKeySecretResponse(**_metadata(item).model_dump(), api_key=raw_key)


@router.post("/{name}/revoke", response_model=MCPKeyMetadata,
             summary="Revoke an MCP API key")
async def revoke_mcp_key(name: str) -> MCPKeyMetadata:
    try:
        return _metadata(_service().revoke_key(name=name))
    except KeyNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc


__all__ = ["router"]
