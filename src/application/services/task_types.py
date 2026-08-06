"""
Shared task / error contract types for the application layer.

Moved out of ``src.web_api.schemas`` (M5 review): the application layer
(``IngestionService`` / ``TaskTracker`` / ``QueryService``) must be
reused by CLI, MCP, Streamlit and Web API, so it must **not** import the
Web API layer. These Pydantic models are the contract types; the Web API
schemas re-export them so FastAPI response models and the generated
OpenAPI stay unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskError(BaseModel):
    """Structured error for async business operations.

    Lives inside :class:`TaskStatusResponse` and
    :class:`DocumentDetail` (as ``last_error``). Same shape and
    branching rules as the top-level HTTP :class:`ErrorDetail`, but
    scoped to "this thing I asked you to do, eventually failed" rather
    than "this request couldn't even be processed".
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code": "UPSTREAM_ERROR",
                    "message": "Embedding provider failed",
                    "details": {"provider": "openai", "status_code": 502},
                },
                {
                    "code": "UNSUPPORTED_FILE_TYPE",
                    "message": "File content-type 'text/plain' is not allowed",
                    "details": {"received_content_type": "text/plain"},
                },
            ]
        }
    )

    code: str = Field(
        ...,
        description=(
            "Stable machine-readable code. Frontend branches on this, "
            "NEVER on ``message``. Same vocabulary as the top-level "
            "HTTP error envelope (``ErrorDetail.code``)."
        ),
        examples=["UPSTREAM_ERROR"],
    )
    message: str = Field(..., description="Human-readable error description.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured context (e.g. provider name, status code).",
    )


TaskStage = Literal[
    "load",       # parse source file
    "split",      # chunk the text
    "transform",  # LLM refine / metadata enrich / image caption
    "embed",      # dense + sparse embedding
    "upsert",     # write to vector store + BM25 + image store
]
"""Top-level ingestion pipeline stages.

Reflects the real backend stages (see ``src/ingestion/pipeline.py``) — not
an aspirational contract. New stages (e.g. ``validate``) are only added
when the backend actually emits events for them.
"""


TaskStatus = Literal[
    "pending",     # accepted, not yet picked up
    "running",     # worker actively processing
    "succeeded",   # terminal: success
    "failed",      # terminal: permanent failure
    "cancelled",   # terminal: user cancellation
    "skipped",     # terminal: duplicate of an already-ingested file (M5)
]
"""Terminal and in-flight states.

``retrying`` is intentionally absent in v0.1 — only added when real
auto-retry is implemented, together with ``attempt`` / ``max_attempts`` /
``next_retry_at``.

``skipped`` (M5): a task whose pipeline run detected the file's SHA256
already marked ``success`` in the integrity DB for this collection. It is
a *terminal* state — the frontend polling loop must treat it like
``succeeded``/``failed`` (stop polling).
"""


class TaskProgress(BaseModel):
    """Structured progress for the current pipeline stage.

    ``percent`` is the canonical value the frontend should display
    (0–100). ``current``/``total`` stay for diagnostic views and are
    nullable when the backend can't predict totals (e.g. some LLM
    transforms).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "stage": "embed",
                "current": 32,
                "total": 100,
                "percent": 32,
                "message": "Embedding chunks",
            }
        }
    )

    stage: TaskStage
    current: int | None = Field(
        None,
        ge=0,
        description="Items processed in this stage; ``null`` when unknown.",
    )
    total: int | None = Field(
        None,
        ge=0,
        description="Items expected in this stage; ``null`` when unknown.",
    )
    percent: int = Field(
        ...,
        ge=0,
        le=100,
        description=(
            "Computed by the backend (0–100). Frontend must NOT recompute; "
            "different stages use different denominators."
        ),
    )
    message: str | None = Field(
        None,
        description=(
            "Short human-readable hint. Frontend may display but must NOT "
            "branch business logic on it."
        ),
    )


__all__ = [
    "TaskError",
    "TaskProgress",
    "TaskStage",
    "TaskStatus",
]
