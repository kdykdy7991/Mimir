"""Security-safe HTTP mutation/denial audit boundary."""

from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.web_api.middleware.request_id import get_request_id

_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        should_record = request.method in _MUTATING or response.status_code in {401, 403}
        if not should_record or not request.url.path.startswith("/api/v1/"):
            return response
        services = getattr(request.app.state, "application_services", None)
        audit = getattr(services, "audit", None) if services is not None else None
        if audit is None:
            return response
        route = request.scope.get("route")
        template = getattr(route, "path", "unmatched")
        params = request.path_params
        collection_id = params.get("collection_id") or params.get("id")
        resource_id = next((
            str(params[key]) for key in (
                "revision_id", "target_revision_id", "document_id", "task_id",
                "tag_id", "folder_id", "name",
            ) if key in params
        ), None)
        actor_id = request.headers.get("X-Actor-ID", "trusted-admin")[:200]
        outcome = (
            "denied" if response.status_code in {401, 403}
            else "success" if response.status_code < 400
            else "rejected"
        )
        audit.append(
            actor_type="admin", actor_id=actor_id,
            action=f"http.{request.method.lower()}",
            resource_type=str(template), resource_id=resource_id,
            collection_id=str(collection_id) if collection_id is not None else None,
            outcome=outcome, request_id=get_request_id(request),
            metadata={"status_code": response.status_code},
        )
        return response


__all__ = ["AuditMiddleware"]
