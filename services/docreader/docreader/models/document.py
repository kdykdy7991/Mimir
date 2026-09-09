# SPDX-License-Identifier: MIT
#
# Adapted from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/models/document.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# Reference-rewrite per docs/baselines/document-parsing/provenance.md: only the
# parse-result surface (content / images / metadata) is kept; the upstream
# legacy ``Chunk`` model is intentionally NOT migrated (out of scope, plan §3.2).
"""Service-local parse-result model for SKDY DocReader."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Document:
    """Parse result: markdown ``content`` + image refs + metadata.

    Mirrors the essential contract of upstream ``docreader.models.document.Document``
    (``content`` markdown, ``images`` mapping, ``metadata``) without the legacy
    Chunk type. Images map a stable key/relative path to raw bytes.
    """

    content: str = ""
    images: dict[str, bytes] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def set_content(self, content: str) -> None:
        self.content = content

    def get_content(self) -> str:
        return self.content

    def is_valid(self) -> bool:
        return self.content != ""

    def to_json(self, **kwargs: Any) -> str:
        """Serialize for debugging; image bytes are replaced with sizes only
        so no base64 leaks into logs (plan §6.1/§14)."""
        data = dict(kwargs)
        data["content"] = self.content
        data["images"] = {k: len(v) for k, v in self.images.items()}
        data["metadata"] = self.metadata
        return json.dumps(data, ensure_ascii=False)