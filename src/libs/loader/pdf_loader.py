"""
PDF loader implementation.

Extracts text + embedded images from a PDF and returns a
``core.types.Document`` that satisfies the C1 contract:

- ``metadata.source_path`` is always set.
- ``metadata.images`` is a list of ``ImageRef`` (one per image on
  every page), or empty when the PDF has no images.
- For each image, a ``[IMAGE: {image_id}]`` placeholder is inserted
  into ``Document.text`` at the vertical position the image occupies
  on the page (so downstream chunking can keep text and image
  references together).

Image extraction is best-effort: if a per-image failure occurs, the
loader logs a warning and continues with text-only output for that
file. Text extraction failures are fatal (we can't return a useful
Document without text).
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

import pymupdf  # type: ignore[import-untyped]

from src.core.types import Document, ImageRef
from src.libs.loader.base_loader import BaseLoader, LoaderError


logger = logging.getLogger(__name__)

# Default location for extracted images; per-document subdirectory
# is created underneath this.
DEFAULT_IMAGE_DIR = "./data/images"

# Truncation length for the per-doc hash used in image IDs. 16 hex
# chars = 64 bits, more than enough to disambiguate a single doc's
# images, and keeps IDs readable in logs.
DOC_HASH_LEN = 16


class PdfLoader(BaseLoader):
    """
    Load PDF files into ``Document`` objects.

    Args:
        image_dir: Root directory under which a per-document
            subdirectory (``{image_dir}/{doc_hash}/``) is created to
            hold extracted images. The directory is created on first
            use if it doesn't exist.
        image_classifier: Optional image classification settings.
            When provided, images are flagged as decorative based on
            size, position, and duplicate detection rules.
    """

    def __init__(
        self,
        image_dir: str = DEFAULT_IMAGE_DIR,
        image_classifier: Any | None = None,
    ) -> None:
        self.image_dir = image_dir
        self.image_classifier = image_classifier

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load(self, path: str) -> Document:
        file_path = self._require_file(path)
        doc_hash = self._doc_hash(file_path)

        images: list[ImageRef] = []
        text_parts: list[str] = []
        page_count = 0

        # Track duplicate images across pages (e.g. company logos).
        seen_hashes: set[str] = set()

        try:
            with pymupdf.open(str(file_path)) as pdf:
                page_count = pdf.page_count
                for page_idx in range(page_count):
                    page = pdf.load_page(page_idx)
                    page_text, page_images = self._process_page(
                        pdf, page, page_idx, doc_hash, seen_hashes,
                    )
                    text_parts.append(page_text)
                    images.extend(page_images)
        except Exception as exc:  # noqa: BLE001 — we want a uniform surface
            raise LoaderError(
                f"Failed to parse PDF {file_path}: {exc}"
            ) from exc

        full_text = "\n\n".join(text_parts)

        # Build metadata per the C1 contract.
        metadata: dict[str, Any] = {
            "source_path": str(file_path),
            "doc_type": "pdf",
            "page_count": page_count,
            "doc_hash": doc_hash,
        }
        if images:
            metadata["images"] = [img.to_dict() for img in images]

        return Document(
            id=doc_hash,  # full-length SHA256 hex (64 chars)
            text=full_text,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Per-page processing
    # ------------------------------------------------------------------
    def _process_page(
        self,
        pdf: pymupdf.Document,
        page: pymupdf.Page,
        page_idx: int,
        doc_hash: str,
        seen_hashes: set[str] | None = None,
    ) -> tuple[str, list[ImageRef]]:
        """
        Extract text + images from a single page, merging them in
        vertical reading order.

        Returns:
            (page_text, images_on_page) where ``page_text`` has
            ``[IMAGE: {image_id}]`` placeholders interleaved with
            text lines, and ``images_on_page`` is the list of
            ``ImageRef``s (empty if no images / all failed).
        """
        page_rect = page.rect
        # 1) Collect text lines with their bboxes.
        text_lines: list[tuple[float, float, str]] = []
        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:  # only text blocks
                continue
            for line in block.get("lines", []):
                line_text = "".join(
                    span.get("text", "") for span in line.get("spans", [])
                ).strip()
                if not line_text:
                    continue
                bx0, by0, _, _ = line["bbox"]
                # Insertion key: (y_top, x_left) — top of page = y0
                text_lines.append((by0, bx0, line_text))

        # 2) Collect images with their bboxes, extract bytes.
        # ``get_image_info`` is the modern API; it returns one entry
        # per visible image occurrence with bbox + xref.
        images: list[ImageRef] = []
        image_items: list[tuple[float, float, str]] = []  # (y, x, placeholder)
        for seq, img_info in enumerate(page.get_image_info(xrefs=True)):
            xref = img_info.get("xref")
            if not xref:
                continue
            bbox = img_info.get("bbox")
            if not bbox:  # image not visible (e.g. mask only)
                continue
            ix0, iy0, _, _ = bbox

            image_id = f"{doc_hash}_{page_idx}_{seq}"
            placeholder = f"[IMAGE: {image_id}]"

            # Extraction, classification and saving are all best-effort:
            # any failure drops this one image (no ImageRef, no
            # placeholder) while the rest of the page loads normally.
            try:
                info = pdf.extract_image(xref)
                data = info.get("image")
                if not data:
                    raise LoaderError(f"Empty image payload for xref {xref}")

                # Classify before saving. Decorative images are dropped
                # entirely: no disk write, no placeholder, no metadata.
                is_content, reason = self._classify_image(
                    bbox=bbox,
                    page_rect=page_rect,
                    image_bytes=data,
                    seen_hashes=seen_hashes,
                )
                if not is_content:
                    logger.debug(
                        "Skipping decorative image %s on page %d: %s",
                        image_id, page_idx, reason,
                    )
                    continue

                img = self._save_image_ref(
                    image_id=image_id,
                    doc_hash=doc_hash,
                    bbox=bbox,
                    page_idx=page_idx,
                    ext=info.get("ext", "png"),
                    data=data,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.warning(
                    "Failed to extract image %s on page %d: %s",
                    image_id, page_idx, exc,
                )
                continue

            images.append(img)
            image_items.append((iy0, ix0, placeholder))

        # 3) Merge text + image placeholders in reading order.
        merged: list[tuple[float, float, str]] = text_lines + image_items
        # Stable sort by (y_top, x_left). When two items share the
        # same y, x-order keeps multi-column text correct for
        # typical left-to-right layouts.
        merged.sort(key=lambda item: (item[0], item[1]))

        page_text = "\n".join(payload for _, _, payload in merged)
        return page_text, images

    # ------------------------------------------------------------------
    # Image extraction
    # ------------------------------------------------------------------
    def _save_image_ref(
        self,
        *,
        image_id: str,
        doc_hash: str,
        bbox: tuple[float, float, float, float],
        page_idx: int,
        ext: str,
        data: bytes,
    ) -> ImageRef:
        """
        Save the image bytes to ``{image_dir}/{doc_hash}/{image_id}.{ext}``
        and return the corresponding ``ImageRef``.

        This is called only for images that have already passed the
        classifier and are considered content.
        """
        target_dir = Path(self.image_dir) / doc_hash
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{image_id}.{ext}"
        target_path.write_bytes(data)

        # text_offset / text_length are filled in later by the
        # chunker (which sees the final Document.text). At load
        # time we don't know the exact offset yet, so we leave them
        # at 0 — downstream C4 has all the information it needs.
        return ImageRef(
            id=image_id,
            path=str(target_path),
            page=page_idx,
            text_offset=0,
            text_length=0,
            position={
                "x0": bbox[0], "y0": bbox[1],
                "x1": bbox[2], "y1": bbox[3],
            },
            is_content=True,
            classification_reason=None,
        )

    # ------------------------------------------------------------------
    # Image classification
    # ------------------------------------------------------------------
    def _classify_image(
        self,
        bbox: tuple[float, float, float, float],
        page_rect: pymupdf.Rect,
        image_bytes: bytes,
        seen_hashes: set[str] | None,
    ) -> tuple[bool, str | None]:
        """
        Apply hard filters to decide if an image is content or decorative.

        Returns (is_content, reason). Decorative images get a reason
        string; content images get ``reason=None``.
        """
        cfg = self.image_classifier
        if cfg is None or not getattr(cfg, "enabled", False):
            return True, None

        hard = getattr(cfg, "hard_filter", None)
        if hard is None or not getattr(hard, "enabled", True):
            return True, None

        ix0, iy0, ix1, iy1 = bbox
        img_width = ix1 - ix0
        img_height = iy1 - iy0
        img_area = img_width * img_height
        page_area = page_rect.width * page_rect.height
        area_ratio = img_area / page_area if page_area > 0 else 0.0

        # Size filter.
        if (
            getattr(hard, "size_enabled", True)
            and (
                img_width < hard.min_width
                or img_height < hard.min_height
                or area_ratio < hard.min_area_ratio
            )
        ):
            return False, (
                f"size_filter: {img_width:.1f}x{img_height:.1f} "
                f"(ratio {area_ratio:.4f})"
            )

        # Position filter.
        # An image is considered decorative if it starts inside the
        # header band or ends inside the footer band. This catches
        # logos and page furniture that may extend slightly beyond a
        # strict margin while avoiding false positives on large body
        # diagrams that merely touch the margin.
        if getattr(hard, "position_enabled", False):
            page_height = page_rect.height
            header_line = page_height * hard.header_ratio
            footer_line = page_height * (1.0 - hard.footer_ratio)
            if iy0 < header_line:
                return False, "position_filter: header"
            if iy1 > footer_line:
                return False, "position_filter: footer"

        # Duplicate filter.
        if getattr(hard, "duplicate_enabled", True) and seen_hashes is not None:
            h = hashlib.sha256(image_bytes).hexdigest()
            if h in seen_hashes:
                return False, "duplicate_filter: repeated_image"
            seen_hashes.add(h)

        return True, None

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------
    @staticmethod
    def _doc_hash(path: Path) -> str:
        """SHA256 of the file contents, in hex. Used as Document.id."""
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()[:DOC_HASH_LEN]


# ---------------------------------------------------------------------------
# Helper: extract all image IDs mentioned in a Document's text.
# Useful for downstream chunks (C4) and for tests.
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\[IMAGE:\s*([^\]]+?)\s*\]")


def extract_image_mentions(text: str) -> list[str]:
    """
    Return the list of image IDs referenced by ``[IMAGE: id]``
    placeholders in ``text``, in order of appearance.
    """
    return _PLACEHOLDER_RE.findall(text)
