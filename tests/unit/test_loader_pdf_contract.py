"""
Contract tests for BaseLoader + PdfLoader (C3).

Tests dynamically generate two PDF fixtures (a text-only PDF and a
PDF with an embedded image) using pymupdf itself, so the test
suite has no binary fixtures to maintain. The acceptance criteria
from the spec are exercised end-to-end:

- simple.pdf → Document with text, no images, source_path set
- with_images.pdf → Document with text + [IMAGE: id] placeholders
                     inserted at the correct vertical positions,
                     plus matching ImageRefs and image files on disk
- image extraction failures degrade gracefully (text still returned)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

from src.core.settings import ImageClassifierSettings
from src.core.types import ImageRef
from src.libs.loader import (
    BaseLoader,
    LoaderError,
    PdfLoader,
    extract_image_mentions,
)


# ---------------------------------------------------------------------------
# PDF generation helpers
# ---------------------------------------------------------------------------

def _build_pdf(path: Path, pages: list[dict]) -> None:
    """
    Build a PDF at ``path``. ``pages`` is a list of dicts, one per
    page, with optional keys:

    - ``"text"``: list of (text, x, y) tuples written top-to-bottom.
    - ``"image"``: optional (xref_image_bytes, x, y, w, h) — a
      minimal PNG raster drawn at the given position.
    """
    doc = pymupdf.open()
    for page_spec in pages:
        page = doc.new_page()
        # Text
        for text, x, y in page_spec.get("text", []):
            page.insert_text((x, y), text)
        # Image
        if "image" in page_spec:
            img_bytes, ix, iy, iw, ih = page_spec["image"]
            page.insert_image(
                pymupdf.Rect(ix, iy, ix + iw, iy + ih),
                stream=img_bytes,
            )
    doc.save(str(path))
    doc.close()


def _minimal_png(width: int = 8, height: int = 8) -> bytes:
    """A solid-color PNG (8×8 black) — just enough to embed."""
    import io
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    # Single IDAT: each row prefixed with filter byte 0, then 8 bytes of 0x00
    raw = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    idat = zlib.compress(raw)
    iend = b""
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", iend)


@pytest.fixture
def simple_pdf(tmp_path) -> Path:
    """One-page text-only PDF."""
    p = tmp_path / "simple.pdf"
    _build_pdf(p, [
        {
            "text": [
                ("Hello, world.", 72, 72),
                ("This is page one.", 72, 100),
            ],
        },
    ])
    return p


@pytest.fixture
def two_page_text_pdf(tmp_path) -> Path:
    """Two-page text-only PDF — exercises page iteration."""
    p = tmp_path / "two_page.pdf"
    _build_pdf(p, [
        {"text": [("Page one content.", 72, 72)]},
        {"text": [("Page two content.", 72, 72)]},
    ])
    return p


@pytest.fixture
def pdf_with_image(tmp_path) -> Path:
    """One-page PDF with one embedded image, sandwiched between text."""
    p = tmp_path / "with_images.pdf"
    _build_pdf(p, [
        {
            "text": [
                ("Caption above the image.", 72, 72),
            ],
            "image": (_minimal_png(), 72, 200, 100, 100),
        },
    ])
    return p


@pytest.fixture
def pdf_image_only(tmp_path) -> Path:
    """One-page PDF with ONLY an image (no text blocks)."""
    p = tmp_path / "image_only.pdf"
    _build_pdf(p, [
        {
            "image": (_minimal_png(), 72, 72, 200, 200),
        },
    ])
    return p


@pytest.fixture
def loader(tmp_path) -> PdfLoader:
    """A PdfLoader pointed at a tmp image directory."""
    return PdfLoader(image_dir=str(tmp_path / "images"))


# ---------------------------------------------------------------------------
# Abstract interface contract
# ---------------------------------------------------------------------------

class TestAbstractLoader:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseLoader()  # type: ignore[abstract]

    def test_subclass_must_implement_load(self):
        class HalfBaked(BaseLoader):
            pass

        with pytest.raises(TypeError):
            HalfBaked()  # type: ignore[abstract]

    def test_load_many_preserves_order(self, loader, simple_pdf, two_page_text_pdf):
        docs = loader.load_many([str(simple_pdf), str(two_page_text_pdf)])
        assert len(docs) == 2
        assert docs[0].metadata["page_count"] == 1
        assert docs[1].metadata["page_count"] == 2


# ---------------------------------------------------------------------------
# Simple (text-only) PDF
# ---------------------------------------------------------------------------

class TestSimplePdf:
    def test_load_returns_document(self, loader, simple_pdf):
        doc = loader.load(str(simple_pdf))
        assert doc.id  # non-empty
        assert isinstance(doc.text, str)
        assert doc.text.strip()  # non-empty

    def test_document_id_is_stable_sha_prefix(self, loader, simple_pdf):
        """Document.id must be SHA256 of the file, first 16 hex chars."""
        doc = loader.load(str(simple_pdf))
        assert len(doc.id) == 16
        assert all(c in "0123456789abcdef" for c in doc.id)
        # Loading the same file twice gives the same id.
        doc2 = loader.load(str(simple_pdf))
        assert doc.id == doc2.id

    def test_metadata_source_path_is_set(self, loader, simple_pdf):
        doc = loader.load(str(simple_pdf))
        assert doc.metadata["source_path"] == str(simple_pdf)

    def test_text_contains_expected_content(self, loader, simple_pdf):
        doc = loader.load(str(simple_pdf))
        assert "Hello, world." in doc.text
        assert "This is page one." in doc.text

    def test_no_images_key_when_no_images(self, loader, simple_pdf):
        """Per spec: no images → no 'images' key (or empty list)."""
        doc = loader.load(str(simple_pdf))
        assert "images" not in doc.metadata or doc.metadata["images"] == []

    def test_page_count_recorded(self, loader, simple_pdf):
        doc = loader.load(str(simple_pdf))
        assert doc.metadata["page_count"] == 1

    def test_multi_page_text_preserved(self, loader, two_page_text_pdf):
        doc = loader.load(str(two_page_text_pdf))
        assert doc.metadata["page_count"] == 2
        assert "Page one content." in doc.text
        assert "Page two content." in doc.text


# ---------------------------------------------------------------------------
# PDF with embedded images
# ---------------------------------------------------------------------------

class TestPdfWithImage:
    def test_text_contains_image_placeholder(self, loader, pdf_with_image):
        doc = loader.load(str(pdf_with_image))
        placeholders = extract_image_mentions(doc.text)
        assert len(placeholders) == 1
        image_id = placeholders[0]
        # ID format: {doc_hash}_{page}_{seq}
        assert image_id.startswith(doc.id + "_0_")

    def test_placeholder_appears_after_caption(self, loader, pdf_with_image):
        """Image below caption → placeholder must come after the caption."""
        doc = loader.load(str(pdf_with_image))
        cap_pos = doc.text.find("Caption above the image.")
        placeholder_pos = doc.text.find("[IMAGE:")
        assert 0 <= cap_pos < placeholder_pos, (
            f"Expected caption before placeholder, got: {doc.text!r}"
        )

    def test_image_file_saved_to_expected_path(self, loader, pdf_with_image, tmp_path):
        doc = loader.load(str(pdf_with_image))
        images = doc.metadata["images"]
        assert len(images) == 1
        img = images[0]
        # Spec: data/images/{doc_hash}/... — but we use a tmp dir here
        # so the structure is {image_dir}/{doc_hash}/{image_id}.{ext}
        expected_dir = Path(loader.image_dir) / doc.id
        expected_path = expected_dir / Path(img["path"]).name
        assert expected_path == Path(img["path"])
        assert expected_path.exists()
        assert expected_path.stat().st_size > 0

    def test_image_ref_has_required_fields(self, loader, pdf_with_image):
        doc = loader.load(str(pdf_with_image))
        img = doc.metadata["images"][0]
        assert img["id"]
        assert img["path"]
        # id matches what's in the placeholder
        assert img["id"] in doc.text
        # position was populated
        pos = img["position"]
        assert pos is not None
        for k in ("x0", "y0", "x1", "y1"):
            assert k in pos
        # ImageRef round-trips through the dataclass
        ref = ImageRef.from_dict(img)
        assert ref.id == img["id"]
        assert ref.path == img["path"]

    def test_image_only_pdf_loads(self, loader, pdf_image_only):
        """PDF with only an image and no text still works."""
        doc = loader.load(str(pdf_image_only))
        assert doc.metadata["page_count"] == 1
        assert "images" in doc.metadata
        assert len(doc.metadata["images"]) == 1
        # No text, but the placeholder is still in the text field
        assert "[IMAGE:" in doc.text

    def test_extract_image_mentions_helper(self, loader, pdf_with_image):
        doc = loader.load(str(pdf_with_image))
        mentions = extract_image_mentions(doc.text)
        assert len(mentions) == 1
        assert mentions[0] == doc.metadata["images"][0]["id"]


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_image_extraction_failure_does_not_break_text(
        self, loader, pdf_with_image
    ):
        """If saving an image raises, text must still load successfully."""
        with patch.object(
            PdfLoader, "_save_image_ref",
            side_effect=RuntimeError("simulated extraction failure"),
        ):
            doc = loader.load(str(pdf_with_image))
        # Text is intact
        assert "Caption above the image." in doc.text
        # No images recorded (failed extraction → dropped from metadata)
        assert doc.metadata.get("images", []) == []
        # The placeholder is also dropped from the text
        assert "[IMAGE:" not in doc.text

    def test_partial_extraction_failure(self, loader, tmp_path):
        """Two images: first fails, second succeeds."""
        # Build a PDF with two images.
        p = tmp_path / "two_images.pdf"
        _build_pdf(p, [
            {
                "text": [("Top", 72, 72)],
                "image": (_minimal_png(), 72, 100, 50, 50),
            },
            {
                "text": [("Bottom", 72, 72)],
                "image": (_minimal_png(), 72, 100, 50, 50),
            },
        ])

        original = PdfLoader._save_image_ref
        call_count = {"n": 0}

        def flaky(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first image fails")
            return original(self, **kwargs)

        with patch.object(PdfLoader, "_save_image_ref", flaky):
            doc = loader.load(str(p))

        # Only the second image should be in metadata.
        assert len(doc.metadata["images"]) == 1
        # Text still has both "Top" and "Bottom".
        assert "Top" in doc.text
        assert "Bottom" in doc.text
        # And exactly one placeholder.
        assert extract_image_mentions(doc.text) == [
            doc.metadata["images"][0]["id"]
        ]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_missing_file_raises(self, loader, tmp_path):
        ghost = tmp_path / "does_not_exist.pdf"
        with pytest.raises(LoaderError, match="not found"):
            loader.load(str(ghost))

    def test_non_pdf_file_raises(self, loader, tmp_path):
        # A text file renamed to .pdf
        fake = tmp_path / "fake.pdf"
        fake.write_text("this is not a pdf")
        with pytest.raises(LoaderError):
            loader.load(str(fake))

    def test_directory_input_raises(self, loader, tmp_path):
        with pytest.raises(LoaderError):
            loader.load(str(tmp_path))


# ---------------------------------------------------------------------------
# Image classification (decorative vs content)
# ---------------------------------------------------------------------------

@pytest.fixture
def classifier_loader(tmp_path) -> PdfLoader:
    """A PdfLoader with image classification enabled (default rules)."""
    cfg = ImageClassifierSettings(enabled=True)
    return PdfLoader(
        image_dir=str(tmp_path / "images"),
        image_classifier=cfg,
    )


@pytest.fixture
def position_classifier_loader(tmp_path) -> PdfLoader:
    """A PdfLoader with the (off-by-default) position filter enabled."""
    cfg = ImageClassifierSettings(enabled=True)
    cfg.hard_filter.position_enabled = True
    return PdfLoader(
        image_dir=str(tmp_path / "images"),
        image_classifier=cfg,
    )


class TestImageClassification:
    """
    Decorative images are dropped outright: no disk write, no text
    placeholder, no entry in ``metadata["images"]``.
    """

    def test_large_content_image_is_content(
        self, classifier_loader, tmp_path,
    ):
        """A reasonably sized image in the body is kept as content."""
        p = tmp_path / "content_image.pdf"
        _build_pdf(p, [
            {
                "text": [("Caption above.", 72, 300)],
                # 200x200 in the middle of an A4 page is content-sized.
                "image": (_minimal_png(200, 200), 72, 400, 200, 200),
            },
        ])
        doc = classifier_loader.load(str(p))
        assert len(doc.metadata["images"]) == 1
        img = doc.metadata["images"][0]
        assert img["is_content"] is True
        assert img["classification_reason"] is None
        assert Path(img["path"]).exists()
        assert extract_image_mentions(doc.text) == [img["id"]]

    def test_small_icon_is_dropped(
        self, classifier_loader, tmp_path,
    ):
        """A tiny image is dropped by the size filter."""
        p = tmp_path / "icon.pdf"
        _build_pdf(p, [
            {
                "text": [("Some text.", 72, 300)],
                # 8x8 icon is far below default 40x40 threshold.
                "image": (_minimal_png(8, 8), 72, 400, 8, 8),
            },
        ])
        doc = classifier_loader.load(str(p))
        assert doc.metadata.get("images", []) == []
        # No placeholder left behind, but the text survives.
        assert "[IMAGE:" not in doc.text
        assert "Some text." in doc.text

    def test_dropped_image_is_not_written_to_disk(
        self, classifier_loader, tmp_path,
    ):
        """A decorative image leaves nothing on disk."""
        p = tmp_path / "logo_not_saved.pdf"
        _build_pdf(p, [
            {
                "text": [("Body.", 72, 400)],
                "image": (_minimal_png(8, 8), 72, 40, 8, 8),
            },
        ])
        classifier_loader.load(str(p))
        image_root = Path(classifier_loader.image_dir)
        written = (
            [f for f in image_root.rglob("*") if f.is_file()]
            if image_root.exists()
            else []
        )
        assert written == []

    def test_header_image_is_dropped(
        self, position_classifier_loader, tmp_path,
    ):
        """An image in the header area is dropped when the filter is on."""
        p = tmp_path / "header_logo.pdf"
        _build_pdf(p, [
            {
                "text": [("Body text.", 72, 400)],
                # Place image near top (A4 page height ~842 pts).
                "image": (_minimal_png(100, 100), 72, 20, 100, 100),
            },
        ])
        doc = position_classifier_loader.load(str(p))
        assert doc.metadata.get("images", []) == []
        assert "Body text." in doc.text

    def test_footer_image_is_dropped(
        self, position_classifier_loader, tmp_path,
    ):
        """An image in the footer area is dropped when the filter is on."""
        p = tmp_path / "footer_image.pdf"
        _build_pdf(p, [
            {
                "text": [("Body text.", 72, 400)],
                # Place image near bottom.
                "image": (_minimal_png(100, 100), 72, 750, 100, 100),
            },
        ])
        doc = position_classifier_loader.load(str(p))
        assert doc.metadata.get("images", []) == []
        assert "Body text." in doc.text

    def test_position_filter_is_off_by_default(
        self, classifier_loader, tmp_path,
    ):
        """
        The position rule is opt-in: with defaults, a header image that
        passes the size filter is kept.
        """
        p = tmp_path / "header_default.pdf"
        _build_pdf(p, [
            {
                "text": [("Body text.", 72, 400)],
                "image": (_minimal_png(100, 100), 72, 20, 100, 100),
            },
        ])
        doc = classifier_loader.load(str(p))
        assert len(doc.metadata["images"]) == 1
        assert doc.metadata["images"][0]["is_content"] is True

    def test_repeated_logo_is_dropped(
        self, classifier_loader, tmp_path,
    ):
        """The same image bytes on a later page is dropped as a duplicate."""
        p = tmp_path / "repeated_logo.pdf"
        png = _minimal_png(60, 60)
        _build_pdf(p, [
            {"text": [("Page one.", 72, 400)], "image": (png, 72, 200, 60, 60)},
            {"text": [("Page two.", 72, 400)], "image": (png, 72, 200, 60, 60)},
        ])
        doc = classifier_loader.load(str(p))
        # Only the first occurrence survives.
        assert len(doc.metadata["images"]) == 1
        kept = doc.metadata["images"][0]
        assert kept["is_content"] is True
        assert kept["page"] == 0
        assert extract_image_mentions(doc.text) == [kept["id"]]

    def test_disabled_classifier_keeps_all_content(
        self, tmp_path,
    ):
        """When classification is disabled, every image is content."""
        loader = PdfLoader(image_dir=str(tmp_path / "images"))
        p = tmp_path / "no_classify.pdf"
        _build_pdf(p, [
            {
                "text": [("Body.", 72, 400)],
                "image": (_minimal_png(8, 8), 72, 40, 8, 8),
            },
        ])
        doc = loader.load(str(p))
        img = doc.metadata["images"][0]
        assert img["is_content"] is True
        assert img["classification_reason"] is None
