# SPDX-License-Identifier: MIT
#
# Adapted from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/parser/opendataloader_parser.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# Trimmed per docs/plan §Phase-3:
#   * LOCAL-ONLY. No hybrid remote endpoint (default not enabled); no SSRF URL
#     handling needed because we never contact a remote.
#   * ``Document.images`` holds RAW image bytes (upstream stored base64) to feed
#     our gRPC ``ImageRef.image_data`` directly (same rule as pdf_parser).
#   * Output is run through table_normalize (GFM/clean-HTML/path hygiene).
# Requires Java 11+ on PATH and the ``opendataloader-pdf`` package; each
# ``convert()`` spawns a JVM. When unavailable, ``opendataloader_available``
# reports the reason so the registry can advertise it as not available.
"""Local OpenDataLoader PDF parser (layout-aware markdown, JVM-backed)."""

from __future__ import annotations

import html as html_mod
import logging
import os
import re
import shutil
import tempfile
from typing import Any, Mapping, Optional

from docreader.config import CONFIG
from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.concurrency import parser_worker_limit
from docreader.parser.pdf_parser import PDFScannedParser
from docreader.parser.table_normalize import normalize_markdown

logger = logging.getLogger(__name__)

_MIN_CHARS_PER_PAGE = 20
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_IMAGE_FILE_NUM_RE = re.compile(r"^imageFile(\d+)\.", re.I)


def _java_available() -> tuple[bool, str]:
    if not shutil.which("java"):
        return False, "需要 Java 11+（JRE），请安装并在 PATH 中配置 java"
    return True, ""


def _package_available() -> tuple[bool, str]:
    try:
        import opendataloader_pdf  # noqa: F401
    except ImportError as e:
        return False, f"opendataloader-pdf 未安装: {e}"
    return True, ""


def opendataloader_available(overrides: Optional[Mapping[str, Any]] = None) -> tuple[bool, str]:
    """Availability probe for ListEngines / registry (local-only)."""
    ok, msg = _java_available()
    if not ok:
        return ok, msg
    return _package_available()


def _find_markdown_file(output_dir: str, pdf_stem: str) -> str:
    candidates = []
    for root, _, files in os.walk(output_dir):
        for name in files:
            if name.lower().endswith(".md"):
                candidates.append(os.path.join(root, name))
    if not candidates:
        raise FileNotFoundError(f"OpenDataLoader 未在 {output_dir} 生成 markdown 文件")
    for path in candidates:
        base = os.path.splitext(os.path.basename(path))[0]
        if base == pdf_stem or base.startswith(pdf_stem):
            return path
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _normalize_odl_image_url(raw: str) -> str:
    s = html_mod.unescape((raw or "").strip())
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    s = s.strip().strip("<>").strip().strip('"').strip("'")
    if s.startswith("./"):
        s = s[2:]
    return s.replace("\\", "/")


def _canonical_image_ref(abs_path: str, output_dir: str) -> str:
    rel = os.path.relpath(abs_path, output_dir).replace("\\", "/")
    name = os.path.basename(abs_path)
    if rel.startswith("images/"):
        return rel
    return f"images/{name}"


def _collect_images_under_output(output_dir: str) -> dict[str, bytes]:
    """Collect every extracted image under the output tree (raw bytes)."""
    images: dict[str, bytes] = {}
    for root, _, files in os.walk(output_dir):
        for name in files:
            if not name.lower().endswith(_IMAGE_SUFFIXES):
                continue
            abs_path = os.path.join(root, name)
            ref = _canonical_image_ref(abs_path, output_dir)
            if ref in images:
                continue
            with open(abs_path, "rb") as f:
                images[ref] = f.read()
    return images


def _register_image_alias(aliases: dict[str, str], alias: str, canonical: str) -> None:
    key = _normalize_odl_image_url(alias)
    if key:
        aliases[key] = canonical


def _build_path_alias_map(images: dict[str, bytes]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for ref in images:
        base = os.path.basename(ref)
        variants = [
            ref, base, f"images/{base}", f"<{ref}>",
            f"<images/{base}>", f"&lt;{ref}&gt;", f"&lt;images/{base}&gt;",
        ]
        for variant in variants:
            _register_image_alias(aliases, variant, ref)
    return aliases


def _resolve_image_ref(url: str, aliases: dict[str, str]) -> Optional[str]:
    key = _normalize_odl_image_url(url)
    if not key or key.startswith("data:"):
        return None
    if key in aliases:
        return aliases[key]
    base = os.path.basename(key)
    for candidate in (base, f"images/{base}"):
        if candidate in aliases:
            return aliases[candidate]
    m = _IMAGE_FILE_NUM_RE.match(base)
    if m:
        num = int(m.group(1))
        numbered = sorted(
            (int(_IMAGE_FILE_NUM_RE.match(os.path.basename(ref)).group(1)), ref)
            for ref in set(aliases.values())
            if _IMAGE_FILE_NUM_RE.match(os.path.basename(ref))
        )
        for n, ref in numbered:
            if n == num:
                return ref
        if numbered and 1 <= num <= len(numbered):
            return numbered[num - 1][1]
    return None


def _rewrite_markdown_image_refs(markdown: str, images: dict[str, bytes]) -> str:
    if not images:
        return markdown
    aliases = _build_path_alias_map(images)

    def repl(match: re.Match[str]) -> str:
        alt, raw_url = match.group(1), match.group(2)
        url = raw_url.strip().split()[0] if raw_url else ""
        canonical = _resolve_image_ref(url, aliases)
        if canonical is None:
            return match.group(0)
        return f"![{alt}]({canonical})"

    return _MD_IMAGE_RE.sub(repl, markdown)


def _run_convert(pdf_path: str, output_dir: str, image_dir: str) -> None:
    import opendataloader_pdf

    opendataloader_pdf.convert(
        input_path=pdf_path,
        output_dir=output_dir,
        format="markdown",
        image_output="external",
        image_dir=image_dir,
        quiet=True,
        markdown_with_html=CONFIG.odl_markdown_with_html,
    )


class OpenDataLoaderParser(BaseParser):
    """Parse PDFs with local OpenDataLoader (layout-aware markdown)."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)

    def parse_into_text(self, content: bytes) -> Document:
        ok, msg = opendataloader_available()
        if not ok:
            raise RuntimeError(msg)

        safe_name = os.path.basename(self.file_name) or "document.pdf"
        if not safe_name.lower().endswith(".pdf"):
            safe_name = f"{os.path.splitext(safe_name)[0] or 'document'}.pdf"
        pdf_stem = os.path.splitext(safe_name)[0]

        with parser_worker_limit("opendataloader", CONFIG.odl_max_workers):
            with tempfile.TemporaryDirectory(prefix="skdy-odl-") as tmp_dir:
                pdf_path = os.path.join(tmp_dir, safe_name)
                with open(pdf_path, "wb") as f:
                    f.write(content)
                image_dir = os.path.join(tmp_dir, "images")
                os.makedirs(image_dir, exist_ok=True)

                _run_convert(pdf_path, tmp_dir, image_dir)

                md_path = _find_markdown_file(tmp_dir, pdf_stem)
                with open(md_path, encoding="utf-8", errors="replace") as f:
                    text = f.read()

                images = _collect_images_under_output(tmp_dir)
                text = _rewrite_markdown_image_refs(text, images)

        text = normalize_markdown(text)

        if len(text.strip()) < _MIN_CHARS_PER_PAGE:
            logger.info(
                "OpenDataLoaderParser: %s yielded little text; "
                "falling back to builtin scanned renderer",
                self.file_name,
            )
            return PDFScannedParser(
                file_name=self.file_name, file_type=self.file_type
            ).parse_into_text(content)

        return Document(
            content=text,
            images=images,
            metadata={
                "parser_engine": "opendataloader",
            },
        )