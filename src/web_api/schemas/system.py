"""
DTOs for ``/system/info`` and ``/system/health``.

``SystemInfo`` deliberately omits any field that would leak a Provider
API key — the Web UI only needs to know *whether* something is configured.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProviderStatus(BaseModel):
    """Per-Provider presence + readiness.

    The ``configured`` and ``ready`` booleans are the only signals the
    frontend should depend on. Detailed model names are exposed because
    the Web overview page needs them, but secrets are NEVER returned.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "configured": True,
                "ready": True,
                "model": "gpt-4o-mini",
            }
        }
    )

    configured: bool = Field(
        ...,
        description="Whether the Provider has the required configuration (without exposing the value).",
    )
    ready: bool = Field(
        ...,
        description="Whether the Provider is currently reachable / usable.",
    )
    model: str | None = Field(
        None,
        description="Configured model name, if any. Omitted if not configured.",
    )


class SystemInfo(BaseModel):
    """``GET /system/info`` response.

    Exposes the pluggable component layout + a per-Provider status map.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "app_name": "skdy-rag-server",
                "version": "0.1.0",
                "storage_backend": "chroma",
                "sparse_backend": "bm25",
                "rerank_backend": "none",
                "providers": {
                    "llm": {"configured": True, "ready": True, "model": "gpt-4o-mini"},
                    "embedding": {"configured": True, "ready": True, "model": "text-embedding-3-small"},
                    "vision": {"configured": False, "ready": False, "model": None},
                },
                "features": {
                    "multimodal": False,
                    "image_captioning": False,
                },
            }
        }
    )

    app_name: str = Field(..., description="Service name (skdy-rag-server).")
    version: str = Field(..., description="Service version.")
    storage_backend: str = Field(
        ..., description="Vector store backend identifier (e.g. 'chroma').",
    )
    sparse_backend: str = Field(
        ..., description="Sparse retrieval backend (e.g. 'bm25').",
    )
    rerank_backend: Literal["none", "cross_encoder", "llm"] = Field(
        ..., description="Active rerank backend.",
    )
    providers: dict[str, ProviderStatus] = Field(
        ...,
        description=(
            "Map of component name (llm / embedding / vision) to its status. "
            "NEVER contains credentials."
        ),
    )
    features: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Optional capability flags (e.g. multimodal, image_captioning). "
            "Web UI may use these to hide unsupported actions."
        ),
    )


class DependencyHealth(BaseModel):
    """Per-dependency health for ``/system/health``."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "chroma",
                "status": "ok",
                "detail": None,
            }
        }
    )

    name: str = Field(..., description="Dependency key (e.g. 'chroma', 'bm25').")
    status: Literal["ok", "degraded", "down"] = Field(...)
    detail: str | None = Field(
        None, description="Optional short message for the diagnostic page.",
    )


class SystemHealth(BaseModel):
    """``GET /system/health`` response.

    Top-level ``status`` is the worst of any dependency — frontend should
    show a banner when this is anything other than ``ok``.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ok",
                    "dependencies": [
                        {"name": "chroma", "status": "ok", "detail": None},
                        {"name": "bm25", "status": "ok", "detail": None},
                    ],
                },
                {
                    "status": "degraded",
                    "dependencies": [
                        {"name": "chroma", "status": "ok", "detail": None},
                        {"name": "vision", "status": "down", "detail": "vision provider not configured"},
                    ],
                },
            ]
        }
    )

    status: Literal["ok", "degraded", "down"] = Field(...)
    dependencies: list[DependencyHealth] = Field(default_factory=list)


__all__ = ["ProviderStatus", "SystemInfo", "DependencyHealth", "SystemHealth"]
