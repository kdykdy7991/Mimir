#!/usr/bin/env python3
"""
Diagnostic script — inspect how PdfLoader classifies images in a PDF.

Usage:
    python scripts/diagnose_pdf_images.py /path/to/file.pdf

Output:
    For every image extracted from the PDF, prints:
    - image id
    - is_content flag
    - classification reason (if decorative)
    - page number
    - position bbox
    - image file path on disk
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.settings import load_settings
from src.libs.loader import PdfLoader


# ---------------------------------------------------------------------------
# EDIT HERE: paste your PDF path between the quotes.
# Example: PDF_PATH = "./data/documents/report.pdf"
# If this is empty, you can still pass the path as a command-line argument.
# ---------------------------------------------------------------------------
PDF_PATH = "/home/hello/fsdownload/hermes_data/公司制度&管理办法/关于下发《浙江时空道宇科技有限公司考勤管理办法（2025 版）》的通知.pdf"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect image classification for a PDF."
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        default=PDF_PATH,
        help="Path to the PDF file.",
    )
    parser.add_argument(
        "--config",
        default="./config/settings.yaml",
        help="Path to settings.yaml (default: ./config/settings.yaml).",
    )
    parser.add_argument(
        "--data-dir",
        default="./data",
        help="Root data directory (default: ./data).",
    )
    args = parser.parse_args()

    pdf_path_str = args.pdf or PDF_PATH
    if not pdf_path_str:
        print(
            "Usage: python scripts/diagnose_pdf_images.py <pdf_path>\n"
            "Or edit PDF_PATH at the top of this script.",
            file=sys.stderr,
        )
        return 1

    pdf_path = Path(pdf_path_str)
    if not pdf_path.is_file():
        print(f"File not found: {pdf_path}", file=sys.stderr)
        return 1

    settings = load_settings(args.config)
    print(f"image_classifier.enabled = {settings.ingestion.image_classifier.enabled}")
    print()

    image_dir = Path(args.data_dir) / "images"
    loader = PdfLoader(
        image_dir=str(image_dir),
        image_classifier=settings.ingestion.image_classifier,
    )

    doc = loader.load(str(pdf_path))
    images = doc.metadata.get("images", [])

    print(f"Total images found: {len(images)}")
    print("-" * 80)

    for img in images:
        pos = img.get("position") or {}
        print(f"id:            {img['id']}")
        print(f"is_content:    {img.get('is_content', True)}")
        print(f"reason:        {img.get('classification_reason') or '(none)'}")
        print(f"page:          {img.get('page')}")
        print(
            f"position:      "
            f"x0={pos.get('x0'):.1f}, y0={pos.get('y0'):.1f}, "
            f"x1={pos.get('x1'):.1f}, y1={pos.get('y1'):.1f}"
        )
        print(f"path:          {img['path']}")
        print("-" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
