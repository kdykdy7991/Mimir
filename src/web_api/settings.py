"""
Web API settings.

Centralises knobs that affect the HTTP surface but are not part of the
RAG core configuration (which lives in ``src/core/settings.py``). Kept
in its own module so importing the Web API does not pull in the whole
core config graph.

Environment variables (with ``WEB_API_`` prefix) override defaults —
useful for one-off overrides without touching code:

- ``WEB_API_DATA_DIR`` — root data directory the app services read/write
- ``WEB_API_UPLOAD_MAX_BYTES`` — override upload size cap (per file)
- ``WEB_API_UPLOAD_ALLOWED_MIME`` — comma-separated MIME allow-list
- ``WEB_API_UPLOAD_ALLOWED_EXTENSIONS`` — comma-separated file-extension
  allow-list (M5 double validation)
- ``WEB_API_UPLOAD_MAX_BATCH_FILES`` — max files per batch (M5)
- ``WEB_API_UPLOAD_MAX_BATCH_BYTES`` — max total bytes per batch (M5)
- ``WEB_API_PAGE_LIMIT_DEFAULT`` — default page size
- ``WEB_API_PAGE_LIMIT_MAX`` — maximum page size (hard cap)
- ``WEB_API_REQUEST_TIMEOUT_SECONDS`` — suggested client-side timeout
- ``WEB_API_CORS_ALLOW_ORIGINS`` — comma-separated CORS origins
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import FrozenSet


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"environment variable {name} must be an integer, got {raw!r}"
        ) from exc


def _get_str_set(name: str, default: FrozenSet[str]) -> FrozenSet[str]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


# ---------------------------------------------------------------------------
# Defaults — per the v0.1 contract (+ M5 batch/extension rules)
# ---------------------------------------------------------------------------
DEFAULT_UPLOAD_MAX_BYTES = 20 * 1024 * 1024  # 20 MB per file
DEFAULT_UPLOAD_ALLOWED_MIME: FrozenSet[str] = frozenset({
    "application/pdf",
    "text/markdown",
    "text/plain",
})
DEFAULT_UPLOAD_ALLOWED_EXTENSIONS: FrozenSet[str] = frozenset({
    ".pdf",
    ".md",
    ".markdown",
})
DEFAULT_UPLOAD_MAX_BATCH_FILES = 50
DEFAULT_UPLOAD_MAX_BATCH_BYTES = 1024 * 1024 * 1024  # 1 GB total per batch
DEFAULT_PAGE_LIMIT_DEFAULT = 20
DEFAULT_PAGE_LIMIT_MAX = 100
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_CORS_ALLOW_ORIGINS: FrozenSet[str] = frozenset({"*"})


@dataclass(frozen=True)
class WebAPISettings:
    """Immutable Web API settings. Read once at app boot."""

    data_dir: str = "data"
    upload_max_bytes: int = DEFAULT_UPLOAD_MAX_BYTES
    upload_allowed_mime: FrozenSet[str] = field(
        default_factory=lambda: DEFAULT_UPLOAD_ALLOWED_MIME,
    )
    upload_allowed_extensions: FrozenSet[str] = field(
        default_factory=lambda: DEFAULT_UPLOAD_ALLOWED_EXTENSIONS,
    )
    upload_max_batch_files: int = DEFAULT_UPLOAD_MAX_BATCH_FILES
    upload_max_batch_bytes: int = DEFAULT_UPLOAD_MAX_BATCH_BYTES
    page_limit_default: int = DEFAULT_PAGE_LIMIT_DEFAULT
    page_limit_max: int = DEFAULT_PAGE_LIMIT_MAX
    request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    cors_allow_origins: FrozenSet[str] = field(
        default_factory=lambda: DEFAULT_CORS_ALLOW_ORIGINS,
    )

    @classmethod
    def from_env(cls) -> "WebAPISettings":
        """Read from process env, fall back to defaults."""
        return cls(
            data_dir=os.environ.get("WEB_API_DATA_DIR", "data"),
            upload_max_bytes=_get_int(
                "WEB_API_UPLOAD_MAX_BYTES", DEFAULT_UPLOAD_MAX_BYTES,
            ),
            upload_allowed_mime=_get_str_set(
                "WEB_API_UPLOAD_ALLOWED_MIME", DEFAULT_UPLOAD_ALLOWED_MIME,
            ),
            upload_allowed_extensions=_get_str_set(
                "WEB_API_UPLOAD_ALLOWED_EXTENSIONS",
                DEFAULT_UPLOAD_ALLOWED_EXTENSIONS,
            ),
            upload_max_batch_files=_get_int(
                "WEB_API_UPLOAD_MAX_BATCH_FILES", DEFAULT_UPLOAD_MAX_BATCH_FILES,
            ),
            upload_max_batch_bytes=_get_int(
                "WEB_API_UPLOAD_MAX_BATCH_BYTES", DEFAULT_UPLOAD_MAX_BATCH_BYTES,
            ),
            page_limit_default=_get_int(
                "WEB_API_PAGE_LIMIT_DEFAULT", DEFAULT_PAGE_LIMIT_DEFAULT,
            ),
            page_limit_max=_get_int(
                "WEB_API_PAGE_LIMIT_MAX", DEFAULT_PAGE_LIMIT_MAX,
            ),
            request_timeout_seconds=_get_int(
                "WEB_API_REQUEST_TIMEOUT_SECONDS", DEFAULT_REQUEST_TIMEOUT_SECONDS,
            ),
            cors_allow_origins=_get_str_set(
                "WEB_API_CORS_ALLOW_ORIGINS", DEFAULT_CORS_ALLOW_ORIGINS,
            ),
        )


# Module-level singleton — frozen, safe to read from handlers.
SETTINGS = WebAPISettings.from_env()


__all__ = [
    "DEFAULT_CORS_ALLOW_ORIGINS",
    "DEFAULT_PAGE_LIMIT_DEFAULT",
    "DEFAULT_PAGE_LIMIT_MAX",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_UPLOAD_ALLOWED_EXTENSIONS",
    "DEFAULT_UPLOAD_ALLOWED_MIME",
    "DEFAULT_UPLOAD_MAX_BATCH_BYTES",
    "DEFAULT_UPLOAD_MAX_BATCH_FILES",
    "DEFAULT_UPLOAD_MAX_BYTES",
    "SETTINGS",
    "WebAPISettings",
]
