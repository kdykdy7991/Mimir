"""
Domain exceptions and the unified error envelope.

Service layer raises one of the ``APIError`` subclasses; the FastAPI
exception handlers in ``exception_handlers.py`` convert them into the
single error envelope defined in ``schemas/common.py``.

Stable error codes are part of the API contract — the Web UI branches on
them, never on the human-readable ``message``. See
``TEAM_RESPONSIBILITIES.md §4.1`` for the rationale.
"""

from __future__ import annotations

from typing import Any


class APIError(Exception):
    """Base class for all domain errors that should surface as 4xx/5xx.

    The ``status_code`` follows HTTP semantics; ``code`` is the stable
    machine-readable identifier; ``message`` is human-only.

    Subclasses set sensible defaults but allow override at the call site
    when a more specific message is useful for debugging.
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.__doc__ or self.code
        self.details = details or {}
        super().__init__(self.message)


class BadRequestError(APIError):
    """``400`` — the request was malformed or violated a validation rule."""

    status_code = 400
    code = "BAD_REQUEST"


class NotFoundError(APIError):
    """``404`` — the addressed resource does not exist."""

    status_code = 404
    code = "NOT_FOUND"


class CollectionNotFoundError(NotFoundError):
    """``404`` — specific code for missing collection."""

    code = "COLLECTION_NOT_FOUND"


class DocumentNotFoundError(NotFoundError):
    """``404`` — specific code for missing document."""

    code = "DOCUMENT_NOT_FOUND"


class DocumentDeleteError(APIError):
    """``500`` — coordinated document deletion was only partially completed."""

    code = "DOCUMENT_DELETE_FAILED"


class TaskNotFoundError(NotFoundError):
    """``404`` — specific code for missing ingestion task."""

    code = "TASK_NOT_FOUND"


class QueryNotFoundError(NotFoundError):
    """``404`` — specific code for missing query record."""

    code = "QUERY_NOT_FOUND"


class IngestionNotFoundError(NotFoundError):
    """``404`` — specific code for missing ingestion record."""

    code = "INGESTION_NOT_FOUND"


class ImageNotFoundError(NotFoundError):
    """``404`` — specific code for missing image."""

    code = "IMAGE_NOT_FOUND"


class ConflictError(APIError):
    """``409`` — the operation collides with existing state (e.g. duplicate name)."""

    status_code = 409
    code = "CONFLICT"


class DuplicateCollectionError(ConflictError):
    """``409`` — collection name already exists."""

    code = "COLLECTION_ALREADY_EXISTS"


class UnsupportedFileTypeError(BadRequestError):
    """``400`` — uploaded file type is not allowed."""

    code = "UNSUPPORTED_FILE_TYPE"


class FileTooLargeError(BadRequestError):
    """``400`` — uploaded file exceeds the configured size cap."""

    code = "FILE_TOO_LARGE"


class UpstreamError(APIError):
    """``502`` — a Provider (LLM/embedding/vector store) failed.

    Used to surface 3rd-party failures distinctly from server bugs.
    """

    status_code = 502
    code = "UPSTREAM_ERROR"


class NotReadyError(APIError):
    """``503`` — the service is up but a dependency is down (matches ``/system/health`` 'degraded'/'down')."""

    status_code = 503
    code = "NOT_READY"


class PayloadTooLargeError(BadRequestError):
    """``413`` — uploaded file exceeds the configured size cap.

    Per v0.1 contract, the limit defaults to 20 MB and is configurable
    via ``web_api.upload_max_bytes``.
    """

    status_code = 413
    code = "PAYLOAD_TOO_LARGE"


class UnsupportedMediaTypeError(BadRequestError):
    """``415`` — uploaded file's MIME type is not on the allow-list.

    M1 only accepts ``application/pdf``; M2 may extend via settings.
    """

    status_code = 415
    code = "UNSUPPORTED_MEDIA_TYPE"


__all__ = [
    "APIError",
    "BadRequestError",
    "NotFoundError",
    "CollectionNotFoundError",
    "DocumentNotFoundError",
    "DocumentDeleteError",
    "TaskNotFoundError",
    "QueryNotFoundError",
    "IngestionNotFoundError",
    "ImageNotFoundError",
    "ConflictError",
    "DuplicateCollectionError",
    "UnsupportedFileTypeError",
    "FileTooLargeError",
    "UpstreamError",
    "NotReadyError",
    "PayloadTooLargeError",
    "UnsupportedMediaTypeError",
]
