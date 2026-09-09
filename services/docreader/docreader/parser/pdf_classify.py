# SPDX-License-Identifier: MIT
#
# Adapted from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/parser/pdf_parser.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# Backend rewrite: upstream used pypdfium2; this project uses pymupdf (the
# PDF stack already in the main service). Per docs/plan §10 the algorithm is
# preserved and only the low-level page/image primitives are re-backed.
"""PDF page classification (native text vs scanned image).

The dominant signal for "this page is scanned" is the **image-area coverage
ratio** (image bbox area / page area), not raw character count: a scanned page
is essentially one big image covering the page, even when it carries a (often
low-quality) embedded OCR text layer. Pages are classified independently so
hybrid PDFs (some native, some scanned) are handled correctly.
"""

from __future__ import annotations

import os

# A page whose image objects cover at least this fraction of the page area is
# scanned (image-dominated). Digital pages measure ~0.0–0.05; scanned ~1.0+.
SCAN_IMAGE_AREA_RATIO = float(os.environ.get("DOCREADER_PDF_SCAN_IMAGE_RATIO", 0.5))
# Below this many characters a page is considered to have no usable text layer.
SCAN_MIN_CHARS_PER_PAGE = int(os.environ.get("DOCREADER_PDF_SCAN_MIN_CHARS", 10))
# A near-empty-text page is rendered as an image only if it has image content.
LOW_TEXT_IMAGE_RATIO = 0.1


def classify_page(image_area_ratio: float, text_len: int) -> str:
    """Classify a page as ``"scanned"`` or ``"text"``.

    Image-area coverage is the primary signal; a sparse text layer combined
    with some image content is the secondary signal (ported verbatim from
    upstream ``_classify_page``).
    """
    if image_area_ratio >= SCAN_IMAGE_AREA_RATIO:
        return "scanned"
    if text_len < SCAN_MIN_CHARS_PER_PAGE and image_area_ratio >= LOW_TEXT_IMAGE_RATIO:
        return "scanned"
    return "text"


def page_image_area_ratio(page) -> float:
    """Fraction of page area covered by image objects (pymupdf-backed).

    Overlapping images can push the ratio above 1.0; callers only compare it
    against a threshold so that is harmless.
    """
    width, height = page.mediabox_size
    page_area = float(width) * float(height)
    if page_area <= 0:
        return 0.0
    image_area = 0.0
    for info in page.get_image_info():
        bbox = info.get("bbox")
        if not bbox:
            continue
        x0, y0, x1, y1 = bbox
        image_area += abs((x1 - x0) * (y1 - y0))
    return image_area / page_area


def page_text(page) -> str:
    """Plain top-to-bottom text extraction (fallback path, pymupdf-backed)."""
    return page.get_text("text")