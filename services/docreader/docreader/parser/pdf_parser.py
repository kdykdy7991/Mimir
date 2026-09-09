# SPDX-License-Identifier: MIT
#
# Adapted from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/parser/pdf_parser.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# Algorithms (per-page text/scanned classification, layout reconstruction,
# text post-processing, running-header stripping, markdown assembly, metadata)
# are ported; the pdfium backend is replaced with pymupdf (project PDF stack,
# docs/plan §10). Differences:
#   * ``Document.images`` holds RAW image bytes (upstream stored base64) to feed
#     our gRPC ``ImageRef.image_data`` directly; our service does not OCR/serve
#     base64 itself.
#   * Rendering is serialized on one lock (like upstream) and done in-process.
#   * Vector-figure region clipping (chart regions) is deferred (RENDER_VECTOR
#     default off) so this bridge never emits empty/partial figure data.
"""Per-page routed PDF parser between native text and scanned images (pymupdf)."""

from __future__ import annotations

import io
import logging
import os
import threading
import zlib
from hashlib import md5

import pymupdf

from docreader.config import CONFIG
from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.pdf_classify import (
    page_image_area_ratio,
    page_text,
    classify_page,
)
from docreader.parser.pdf_layout import extract_layout_text, should_prefer_plain
from docreader.parser.pdf_postprocess import postprocess_pdf_text

logger = logging.getLogger(__name__)

# pymupdf is not thread-safe for shared document access across worker threads,
# and it is slower than cheap serialization to reason about; we serialize all
# pdf work behind one lock (mirrors upstream's pdfium lock rationale).
_PDF_LOCK = threading.Lock()

LAYOUT_ORDERING = os.environ.get("DOCREADER_PDF_LAYOUT_ORDERING", "1") in {
    "1", "true", "yes", "y", "on",
}
EXTRACT_EMBEDDED_IMAGES = os.environ.get("DOCREADER_PDF_EXTRACT_EMBEDDED_IMAGES", "1") in {
    "1", "true", "yes", "y", "on",
}
RENDER_VECTOR_FIGURES = os.environ.get("DOCREADER_PDF_RENDER_VECTOR_FIGURES", "0") in {
    "1", "true", "yes", "y", "on",
}
EMBED_MIN_PIXELS = int(os.environ.get("DOCREADER_PDF_EMBED_MIN_PIXELS", 80))
EMBED_MIN_AREA_RATIO = float(os.environ.get("DOCREADER_PDF_EMBED_MIN_AREA_RATIO", 0.01))
EMBED_REPEAT_PAGE_FRAC = float(
    os.environ.get("DOCREADER_PDF_EMBED_REPEAT_PAGE_FRAC", 0.5),
)
EMBED_MAX_IMAGES = int(os.environ.get("DOCREADER_PDF_EMBED_MAX_IMAGES", 50))
# Force all pages scanned (env override; per-upload override supported too).
FORCE_SCANNED_PDF = os.environ.get("DOCREADER_PDF_FORCE_SCANNED", "0") in {
    "1", "true", "yes", "y", "on",
}


def _normalize_image_quality(quality: int) -> int:
    return min(95, max(1, quality))


def render_page_to_jpeg(page, scale: float, quality: int, max_edge: int) -> bytes:
    """Render a page to JPEG bytes, capping the long edge at ``max_edge`` px."""
    width = page.rect.width
    height = page.rect.height
    zoom = scale
    long_edge = max(width, height) * zoom
    if long_edge > max_edge and max_edge > 0:
        zoom *= max_edge / long_edge
    matrix = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False, colorspace=pymupdf.csRGB)
    return pix.tobytes("jpeg", jpg_quality=_normalize_image_quality(quality))


def _extract_page_text_like(page) -> tuple[str, float]:
    """Return (plain_text, image_area_ratio) for a page (pymupdf)."""
    return page_text(page), page_image_area_ratio(page)


def strip_repeating_lines(texts: list, classes: list) -> list:
    """Remove running headers/footers that repeat across most text pages.

    Ported verbatim from upstream ``_strip_repeating_lines``.
    """
    from collections import Counter

    text_indices = [i for i, c in enumerate(classes) if c == "text"]
    if len(text_indices) < 4:
        return list(texts)

    counter: Counter = Counter()
    for i in text_indices:
        lines = [ln.strip() for ln in texts[i].splitlines() if ln.strip()]
        if not lines:
            continue
        for edge in {lines[0], lines[-1]}:
            if len(edge) <= 80:
                counter[edge] += 1

    threshold = max(2, int(len(text_indices) * 0.6))
    repeating = {line for line, count in counter.items() if count >= threshold}
    if not repeating:
        return list(texts)

    cleaned = []
    for i, text in enumerate(texts):
        if classes[i] != "text":
            cleaned.append(text)
            continue
        kept = [ln for ln in text.splitlines() if ln.strip() not in repeating]
        cleaned.append("\n".join(kept))
    return cleaned


def _page_images_pymupdf(doc, i: int) -> list[dict]:
    """Gather embedded image records (xref, width, height) for page ``i``."""
    out = []
    for info in doc.get_page_images(i, full=True):
        xref = info[0]
        width = info[2]
        height = info[3]
        if width <= 0 or height <= 0:
            continue
        out.append({"xref": xref, "width": width, "height": height})
    return out


def extract_embedded_images(doc, classes, base_name: str, quality: int):
    """Extract embedded figures from native text pages (raw bytes), filtered.

    Filters: minimum pixel size, minimum page-area share, cross-page repetition
    (logos/watermarks), and a per-document cap. Returns ``{page_index: [(ref,
    bytes, y_order)]}``.
    """
    if not EXTRACT_EMBEDDED_IMAGES:
        return {}
    text_indices = [i for i, c in enumerate(classes) if c == "text"]
    if not text_indices:
        return {}

    page_areas = {}
    for i in text_indices:
        page = doc.load_page(i)
        pw, ph = page.rect.width, page.rect.height
        page_areas[i] = (pw * ph) if (pw * ph) else 0.0

    # byte-hash -> page list, to detect logos/watermarks repeated across pages
    by_hash: dict[str, list] = {}
    selected: dict[int, list] = {}
    total = 0
    repeat_threshold = max(2, int(len(text_indices) * EMBED_REPEAT_PAGE_FRAC))

    for i in text_indices:
        area = page_areas.get(i, 0.0)
        picked = []
        seen_xref = set()
        for img in _page_images_pymupdf(doc, i):
            if total >= EMBED_MAX_IMAGES:
                break
            xref = img["xref"]
            if xref in seen_xref:
                continue
            seen_xref.add(xref)
            if min(img["width"], img["height"]) < EMBED_MIN_PIXELS:
                continue
            if area > 0 and (img["width"] * img["height"]) / area < EMBED_MIN_AREA_RATIO:
                continue
            try:
                extracted = doc.extract_image(xref)
            except Exception:  # noqa: BLE001
                continue
            raw = extracted.get("image")
            if not raw:
                continue
            digest = md5(raw).hexdigest()
            by_hash.setdefault(digest, []).append(i)
            name = f"{base_name}_img_{i+1}_{xref}.{extracted.get('ext', 'png')}"
            ref_path = f"images/{name}"
            # y-order from the lowest bbox (top-most on page, bottom-left origin)
            page = doc.load_page(i)
            infos = [b for b in page.get_image_info() if b.get("xref") == xref]
            y = min((b["bbox"][3] for b in infos), default=0.0)
            picked.append({"ref": ref_path, "bytes": raw, "y": y, "hash": digest})
            total += 1

        if picked:
            selected.setdefault(i, []).extend(picked)

    # Drop images that repeat on >= repeat_threshold text pages (running logos).
    drop = {h for h, pages in by_hash.items() if len(pages) >= repeat_threshold}
    result: dict[int, list] = {}
    for i, picked in selected.items():
        kept = [
            (p["ref"], p["bytes"], p["y"])
            for p in picked if p["hash"] not in drop
        ]
        if kept:
            result[i] = kept
    return result


class PDFParser(BaseParser):
    """Per-page router between native text extraction and scanned rendering.

    Native text page -> keep its (layout-aware) text layer; scanned page ->
    render to JPEG and tag ``image_source_type=scanned_pdf`` so the caller that
    runs OCR (the main RAG service) can process it. Hybrids interleave in
    reading order.
    """

    def __init__(self, file_name: str = "", file_type=None, **kwargs):
        raw = kwargs.pop("pdf_force_scanned", None)
        super().__init__(file_name=file_name, file_type=file_type, **kwargs)
        if raw is not None:
            self._force_scanned = str(raw).strip().lower() in {
                "1", "true", "yes", "y", "on",
            }
        else:
            self._force_scanned = FORCE_SCANNED_PDF

    def parse_into_text(self, content: bytes) -> Document:
        with _PDF_LOCK:
            return self._route(content)

    def _route(self, content: bytes) -> Document:
        base_name = os.path.splitext(self.file_name or "document")[0]
        scale = max(1, CONFIG.pdf_render_dpi) / 72
        quality = CONFIG.pdf_jpeg_quality

        doc = pymupdf.open(stream=content, filetype="pdf")
        images: dict = {}
        try:
            page_count = doc.page_count
            texts: list = []
            classes: list = []
            for i in range(page_count):
                page = doc.load_page(i)
                plain, ratio = _extract_page_text_like(page)
                if self._force_scanned:
                    cls = "scanned"
                else:
                    cls = classify_page(ratio, len(plain.strip()))
                if cls == "text" and LAYOUT_ORDERING:
                    # layout reconstruction only pays off on native text pages;
                    # skip it when the plain layer already has citations/words.
                    layout = extract_layout_text(page)
                    if layout and not should_prefer_plain(plain, layout):
                        text = layout
                    else:
                        text = plain
                else:
                    text = plain
                texts.append(postprocess_pdf_text(text))
                classes.append(cls)

            texts = strip_repeating_lines(texts, classes)
            scanned_indices = [i for i, c in enumerate(classes) if c == "scanned"]

            for i in scanned_indices:
                page = doc.load_page(i)
                jpeg = render_page_to_jpeg(page, scale, quality, CONFIG.pdf_render_max_edge)
                ref_path = f"images/{base_name}_page_{i + 1}.jpg"
                images[ref_path] = jpeg

            embedded: dict = {}
            vector_figure_count = 0
            embedded_count = 0
            if EXTRACT_EMBEDDED_IMAGES:
                embedded = extract_embedded_images(doc, classes, base_name, quality)
                for i, items in embedded.items():
                    for ref_path, raw, _y in items:
                        images[ref_path] = raw

            blocks = []
            for i in range(page_count):
                if classes[i] == "scanned":
                    page_filename = f"{base_name}_page_{i + 1}.jpg"
                    blocks.append(f"![{page_filename}](images/{page_filename})")
                else:
                    stripped = texts[i].strip()
                    if stripped:
                        blocks.append(stripped)
                    page_images = list(embedded.get(i, []))
                    page_images.sort(key=lambda item: item[2], reverse=True)
                    for ref_path, _raw, _y in page_images:
                        blocks.append(f"![{os.path.basename(ref_path)}]({ref_path})")
                        embedded_count += 1

            content_text = "\n\n".join(blocks).strip()
            metadata = {
                "page_count": page_count,
                "scanned_page_count": len(scanned_indices),
                "text_page_count": page_count - len(scanned_indices),
                "embedded_image_count": embedded_count,
                "vector_figure_count": vector_figure_count,
                "image_source_type": "scanned_pdf" if scanned_indices else "pdf_text_layer",
            }
            logger.info(
                "PDFParser: %s -> %d pages (%d scanned, %d text), embedded=%d",
                self.file_name, page_count, len(scanned_indices),
                page_count - len(scanned_indices), embedded_count,
            )
            return Document(content=content_text, images=images, metadata=metadata)
        finally:
            doc.close()