"""
Image-metadata DTO.

The image bytes themselves are served by ``GET /images/{image_id}``
(not represented in Pydantic — FastAPI returns ``FileResponse``).
This module only describes the JSON side: a future endpoint could
return image metadata without bytes if needed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ImageMeta(BaseModel):
    """Metadata for a single indexed image.

    Returned by future list endpoints (e.g. "all images in this document")
    or as part of a citation (see :class:`CitationImage` in queries.py).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "image_id": "img-001",
                "document_id": "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
                "mime_type": "image/png",
                "width": 1200,
                "height": 800,
                "caption": "Annual leave request flow diagram.",
            }
        }
    )

    image_id: str
    document_id: str = Field(
        ...,
        description="Document UUID (string in this DTO for forward-compat with non-UUID doc IDs).",
    )
    mime_type: str = Field(..., description="MIME type, e.g. 'image/png'.")
    width: int | None = None
    height: int | None = None
    caption: str | None = Field(
        None, description="Generated caption if multimodal enrichment was enabled.",
    )


__all__ = ["ImageMeta"]
