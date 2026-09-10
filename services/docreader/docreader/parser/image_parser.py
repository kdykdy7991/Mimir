# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #7: images).
# Uses Pillow (available in this venv) only for magic/decodability validation.
# A standalone image produces no inherent text: the raw bytes are exposed via
# ``Document.images`` (raw bytes, per the project's ImageRef rule) so the
# Phase-5 vision pipeline can OCR / caption it downstream.
"""Image formats -> Document carrying raw bytes (validation via Pillow)."""

from __future__ import annotations

import io
import logging

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser

logger = logging.getLogger(__name__)

_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


class ImageParser(BaseParser):
    """Validate an image and expose it as a content image for downstream OCR.

    ``parse`` raises if the bytes are not a decodable image (magic validation),
    so a corrupt/garbage payload is never accepted as a silent success.
    """

    def parse_into_text(self, content: bytes) -> Document:
        try:
            import PIL.Image as Image  # type: ignore
            with Image.open(io.BytesIO(content)) as img:
                img.load()  # decode header + data -> real magic/format check
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"invalid image: {exc}") from exc

        ext = (self.file_type or (self.file_name.rsplit(".", 1)[-1] if self.file_name else "png")).lstrip(".")
        mime = _MIME_BY_EXT.get(ext.lower(), "image/png")
        return Document(
            content="",  # no inherent text; OCR happens via the vision pipeline
            images={f"image.{ext or 'png'}": content},
            metadata={"format": ext.lower() or "image", "parser": "builtin", "mime_type": mime},
        )