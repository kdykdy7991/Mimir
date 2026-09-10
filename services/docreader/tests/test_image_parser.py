# SPDX-License-Identifier: MIT
# Phase 6 (images #7): tests. Uses Pillow to build a tiny real image sample.
"""Tests for the image parser (validation + raw-bytes exposure)."""

from __future__ import annotations

import io

import pytest

from docreader.parser.image_parser import ImageParser
from docreader.parser.registry import list_engines, parse_file


def _png_bytes() -> bytes:
    import PIL.Image as Image  # noqa: N817
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_image_validated_and_raw_bytes_exposed() -> None:
    doc = ImageParser(file_name="pic.png", file_type="png").parse(_png_bytes())
    assert doc.metadata["format"] == "png"
    assert doc.metadata["mime_type"] == "image/png"
    assert len(doc.images) == 1
    assert next(iter(doc.images.values())) == _png_bytes()


def test_image_corrupt_rejected() -> None:
    with pytest.raises(ValueError):
        ImageParser(file_name="bad.png", file_type="png").parse(b"not an image at all")


def test_image_formats_registered_and_routed() -> None:
    builtin = next(e for e in list_engines() if e["name"] == "builtin")
    assert {"png", "jpg", "gif", "webp"}.issubset(set(builtin["file_types"]))
    doc = parse_file("pic.png", "png", _png_bytes())
    assert doc.metadata["format"] == "png"
    assert len(doc.images) == 1