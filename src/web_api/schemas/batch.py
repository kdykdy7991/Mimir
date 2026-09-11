"""DTOs for batch document operations (task book B2.6–B2.9).

All four batch endpoints share one per-item result envelope and one batch
response model, so the Web UI can render partial-success uniformly:

- ``BatchItemResult`` — one entry per requested ``document_id`` with
  ``status: "success" | "error"`` and an optional structured ``error``.
- ``BatchResponse`` — the top-level ``{"items": [...]}`` envelope.

Per-item failures never abort the batch: a document that does not resolve,
belongs to a foreign collection, or references a foreign tag/folder is
reported as an item error and the remaining valid items still run.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Batch size caps: tags / move / delete accept up to 100 items; reprocess
# is deliberately capped lower (20) so it cannot overwhelm the ingestion
# service (task book B2.8).
BATCH_MAX_ITEMS = 100
REPROCESS_MAX_ITEMS = 20

BatchTagAction = Literal["add", "remove", "replace"]
"""Tag mutations supported by ``POST .../batch/tags``."""


class BatchItemError(BaseModel):
    """Structured per-item error — same shape as the top-level envelope.

    ``code`` is the stable machine-readable token a client branches on;
    ``message`` is human-only. See ``src/web_api/errors.py``.
    """

    code: str = Field(..., description="Stable machine-readable error code.")
    message: str = Field(..., description="Human-readable error description.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured context (e.g. the foreign tag id).",
    )


class BatchItemResult(BaseModel):
    """One per-document outcome within a batch response."""

    document_id: UUID = Field(..., description="The requested document id.")
    status: Literal["success", "error"] = Field(
        ..., description="``success`` when the item was applied, else ``error``.",
    )
    error: BatchItemError | None = Field(
        None, description="Present only when ``status`` is ``error``.",
    )
    task_id: UUID | None = Field(
        None,
        description="Task id created for this item (reprocess only).",
    )


class BatchResponse(BaseModel):
    """Top-level envelope for all batch document operations."""

    items: list[BatchItemResult] = Field(..., description="One result per requested document.")


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class BatchTagsRequest(BaseModel):
    """Body for ``POST .../documents/batch/tags``."""

    action: BatchTagAction = Field(
        ..., description="``add`` | ``remove`` | ``replace``.",
    )
    tag_ids: list[str] = Field(
        ..., description="Tag ids to apply; every id must belong to the collection.",
    )
    document_ids: list[UUID] = Field(
        ..., description="Target documents (up to {}).".format(BATCH_MAX_ITEMS),
    )


class BatchMoveRequest(BaseModel):
    """Body for ``POST .../documents/batch/move``."""

    folder_id: str | None = Field(
        None, description="Target folder id; ``null`` moves documents to the collection root.",
    )
    document_ids: list[UUID] = Field(
        ..., description="Target documents (up to {}).".format(BATCH_MAX_ITEMS),
    )


class BatchReprocessRequest(BaseModel):
    """Body for ``POST .../documents/batch/reprocess``."""

    document_ids: list[UUID] = Field(
        ..., description="Target documents (up to {}).".format(REPROCESS_MAX_ITEMS),
    )


class BatchDeleteRequest(BaseModel):
    """Body for ``POST .../documents/batch/delete`` (explicit POST action)."""

    document_ids: list[UUID] = Field(
        ..., description="Target documents (up to {}).".format(BATCH_MAX_ITEMS),
    )


__all__ = [
    "BATCH_MAX_ITEMS",
    "REPROCESS_MAX_ITEMS",
    "BatchDeleteRequest",
    "BatchItemError",
    "BatchItemResult",
    "BatchMoveRequest",
    "BatchReprocessRequest",
    "BatchResponse",
    "BatchTagAction",
    "BatchTagsRequest",
]