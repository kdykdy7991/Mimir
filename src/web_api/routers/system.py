"""
``/system/*`` endpoints — service-level metadata and health.

M2 batch 1: wired to ``SystemService`` (application layer). Provider
``ready`` is a M1 stub (always True) until M2 batch 3 wires real probes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.application.composition import ApplicationServices
from src.web_api.dependencies import get_application_services
from src.web_api.schemas.system import (
    DependencyHealth,
    ProviderStatus,
    SystemHealth,
    SystemInfo,
)

router = APIRouter(prefix="/system", tags=["system"])


@router.get(
    "/info",
    response_model=SystemInfo,
    summary="Service + provider status snapshot",
)
async def get_system_info(
    services: ApplicationServices = Depends(get_application_services),
) -> SystemInfo:
    """Return app metadata and per-provider configuration status."""
    view = services.system.get_info()
    return SystemInfo(
        app_name=view.app_name,
        version=view.version,
        storage_backend=view.storage_backend,
        sparse_backend=view.sparse_backend,
        rerank_backend=view.rerank_backend,
        providers={
            name: ProviderStatus(
                configured=status.configured,
                ready=status.ready,
                model=status.model,
            )
            for name, status in view.providers.items()
        },
        features=view.features,
    )


@router.get(
    "/health",
    response_model=SystemHealth,
    summary="Liveness + dependency health",
)
async def get_system_health(
    services: ApplicationServices = Depends(get_application_services),
) -> SystemHealth:
    """Return a coarse health snapshot."""
    view = services.system.get_health()
    return SystemHealth(
        status=view.status,
        dependencies=[
            DependencyHealth(name=d.name, status=d.status, detail=d.detail)
            for d in view.dependencies
        ],
    )


__all__ = ["router"]
