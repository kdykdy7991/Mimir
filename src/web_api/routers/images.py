"""
``/images/{image_id}`` — controlled image access.

M2 batch 3: wired to the document manager's :class:`ImageStorage`.
Image URLs are *relative* (``/api/v1/images/{id}``) — the Web UI
prefixes its API base URL, so the server never needs to know its own
public host.

Served with ``Cache-Control: public, max-age=86400`` (images are
immutable once written — re-saves keep the same id). ETag-based
negotiation is deferred (v0.2 decision).
"""

from __future__ import annotations

import re
from pathlib import Path as FsPath

from fastapi import APIRouter, Depends, Path
from fastapi.responses import FileResponse

from src.application.composition import ApplicationServices
from src.web_api.dependencies import get_application_services
from src.web_api.errors import ImageNotFoundError

router = APIRouter(prefix="/images", tags=["images"])

# Same charset rule as chunk ids — safe to embed in URLs, no path
# separators, no traversal. Defence-in-depth: the real lookup is by
# primary key in the SQLite index, but we never even probe storage with
# a suspicious id.
_IMAGE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_CACHE_CONTROL = "public, max-age=86400"


@router.get(
    "/{image_id}",
    summary="Fetch an indexed image",
    responses={
        200: {"content": {"image/*": {}}},
        404: {"description": "Image not found."},
    },
)
def get_image(
    image_id: str = Path(..., description="Image ID."),
    services: ApplicationServices = Depends(get_application_services),
) -> FileResponse:
    """Return image bytes for the given image ID."""
    if not _IMAGE_ID_RE.fullmatch(image_id):
        raise ImageNotFoundError(
            f"image {image_id!r} does not exist",
            details={"image_id": image_id},
        )

    record = services.document.image_storage.get(image_id)
    if record is None:
        raise ImageNotFoundError(
            f"image {image_id!r} does not exist",
            details={"image_id": image_id},
        )

    path = FsPath(record.file_path)
    if not path.is_file():
        raise ImageNotFoundError(
            f"image {image_id!r} is missing from storage",
            details={"image_id": image_id},
        )

    mime = _MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path,
        media_type=mime,
        headers={"Cache-Control": _CACHE_CONTROL},
    )


__all__ = ["router"]
