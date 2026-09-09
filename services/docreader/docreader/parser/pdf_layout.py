# SPDX-License-Identifier: MIT
#
# Adapted from WeKnora (MIT, Copyright (C) 2025 Tencent):
#   docreader/parser/pdf_parser.py @ 3e6010e7cd3937f289cc1dbadc829e71eb1163f4
# The glyph-level layout reconstruction (XY-cut multi-column reading order,
# heading promotion, margin/watermark stripping, gap-based word spacing,
# plain-vs-layout fallback) is ported VERBATIM from the upstream functions
# listed inline. Only ``page_chars`` was re-backed: upstream pulled glyphs from
# pypdfium2 ``textpage.count_chars()/get_charbox()``; here they come from
# pymupdf ``page.get_text("rawdict")`` (blocks/lines/spans/chars give the same
# ``{"x0","y0","x1","y1","ch"}`` records). Hidden-text render-mode filtering is
# handled in a later cleanup task; pymupdf only emits on-page glyphs, so the
# off-page guard is unnecessary here.
"""Layout-aware, multi-column PDF text extraction (pymupdf backend)."""

from __future__ import annotations

import logging
import os
import re
import statistics

from docreader.parser.pdf_classify import page_text

logger = logging.getLogger(__name__)

# --- Layout knobs (env-overridable, mirrors upstream) ------------------------
LAYOUT_ORDERING = os.environ.get("DOCREADER_PDF_LAYOUT_ORDERING", "1") in {
    "1", "true", "yes", "y", "on",
}
# Below this text length the layout path adds no value; the caller uses plain.
WORD_GAP_WIDTH_RATIO = float(os.environ.get("DOCREADER_PDF_WORD_GAP_WIDTH_RATIO", 0.4))
MARGIN_COL_WIDTH_RATIO = float(
    os.environ.get("DOCREADER_PDF_MARGIN_COL_WIDTH_RATIO", 0.12),
)
MIN_HEADING_LINE_CHARS = int(
    os.environ.get("DOCREADER_PDF_MIN_HEADING_LINE_CHARS", 8),
)
DETECT_HEADINGS = os.environ.get("DOCREADER_PDF_DETECT_HEADINGS", "1") in {
    "1", "true", "yes", "y", "on",
}


def page_chars(page) -> tuple[list, float]:
    """Return ``(chars, page_width)`` built from pymupdf rawdict glyphs.

    Coordinates are normalised to the **bottom-left origin** that the ported
    layout algorithm assumes (upstream used pypdfium2, whose y axis points up).
    pymupdf emits a top-left origin (y points down), so glyph y is flipped here;
    the rest of the algorithm stays byte-for-byte upstream-correct.
    """
    width = float(page.rect.width)
    height = float(page.rect.height)
    chars: list = []
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # 0 = text block (skip image blocks)
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for c in span.get("chars", []):
                    ch = c.get("c", "")
                    if ch in ("\r", "\n", ""):
                        continue
                    x0, y_top, x1, y_bottom = c["bbox"]
                    chars.append(
                        {
                            "x0": x0,
                            "y0": height - y_bottom,  # bottom-left origin
                            "x1": x1,
                            "y1": height - y_top,
                            "ch": ch,
                        },
                    )
    return chars, width


def find_split(items: list, axis: str, min_gap: float):
    """Return a coordinate at the widest clean gap on ``axis`` ('x'), or None.

    Adapted verbatim from upstream ``_find_split``.
    """
    lo, hi = ("x0", "x1") if axis == "x" else ("y0", "y1")
    intervals = sorted(((s[lo], s[hi]) for s in items), key=lambda iv: iv[0])
    cur_end = intervals[0][1]
    best_gap, best_cut = 0.0, None
    for a, b in intervals[1:]:
        gap = a - cur_end
        if gap >= min_gap and gap > best_gap:
            best_gap, best_cut = gap, cur_end + gap / 2
        if b > cur_end:
            cur_end = b
    return best_cut


def split_columns(chars: list, scale: float, width: float, depth: int = 0) -> list:
    """Split glyphs into reading-order columns at full-height gutters."""
    if len(chars) <= 1 or depth > 10:
        return [chars]
    min_gap = max(scale * 2.5, width * 0.04)
    cut = find_split(chars, "x", min_gap)
    if cut is None:
        return [chars]
    left = [c for c in chars if (c["x0"] + c["x1"]) / 2 < cut]
    right = [c for c in chars if (c["x0"] + c["x1"]) / 2 >= cut]
    if not left or not right:
        return [chars]
    return split_columns(left, scale, width, depth + 1) + split_columns(
        right, scale, width, depth + 1,
    )


def column_x_span(chars: list) -> float:
    if not chars:
        return 0.0
    return max(c["x1"] for c in chars) - min(c["x0"] for c in chars)


def _column_single_line_fraction(lines: list) -> float:
    if not lines:
        return 0.0
    single = sum(1 for ln in lines if len(ln["text"]) <= 2)
    return single / len(lines)


def is_artifact_column(chars: list, width: float) -> bool:
    """Detect margin strips / vertical watermarks (e.g. arXiv sidebar)."""
    if not chars or width <= 0:
        return True
    span = column_x_span(chars)
    if span <= 0:
        return True
    lines = group_lines(chars)
    single_frac = _column_single_line_fraction(lines)
    narrow = span / width < MARGIN_COL_WIDTH_RATIO
    if narrow and single_frac >= 0.45:
        return True
    ys = [(c["y0"] + c["y1"]) / 2 for c in chars]
    y_span = max(ys) - min(ys)
    if y_span > span * 3.5 and len(chars) >= 8 and single_frac >= 0.35:
        return True
    return False


def filter_reading_columns(chars: list, scale: float, width: float) -> list:
    """Split into columns and drop margin / watermark strips."""
    cols = split_columns(chars, scale, width)
    kept = [c for c in cols if not is_artifact_column(c, width)]
    if kept:
        return kept
    if len(cols) > 1:
        return [max(cols, key=column_x_span)]
    return cols


def merge_orphan_punctuation_lines(lines: list) -> list:
    """Attach punctuation-only lines to the previous visual line."""
    if not lines:
        return []
    merged: list = []
    for ln in lines:
        t = ln["text"].strip()
        if (
            merged
            and t
            and len(t) <= 4
            and all(c in ".,;:!?…·" or c.isspace() for c in t)
        ):
            suffix = "".join(t.split())
            prev = merged[-1]["text"]
            if suffix and prev and not prev.endswith((" ", "-")):
                merged[-1]["text"] = prev + suffix
            else:
                merged[-1]["text"] = (prev + " " + t).strip()
            continue
        merged.append(dict(ln))
    return merged


def join_line_glyphs(ln_sorted: list) -> str:
    """Join a visual line's glyphs, inferring word spaces from horizontal gaps."""
    if not ln_sorted:
        return ""
    widths = [c["x1"] - c["x0"] for c in ln_sorted if c["x1"] > c["x0"]]
    med_w = statistics.median(widths) if widths else 1.0
    gap_threshold = med_w * WORD_GAP_WIDTH_RATIO

    parts: list[str] = []
    for i, cur in enumerate(ln_sorted):
        ch = cur["ch"]
        if i == 0:
            parts.append(ch)
            continue
        prev = ln_sorted[i - 1]
        if ch.isspace() or prev["ch"].isspace():
            if not ch.isspace() or (parts and not parts[-1].endswith(" ")):
                parts.append(ch)
            continue
        if cur["x0"] - prev["x1"] > gap_threshold:
            parts.append(" ")
        parts.append(ch)
    return "".join(parts).strip()


def group_lines(chars: list) -> list:
    """Group a column's glyphs into lines (top-to-bottom, glyphs sorted by x)."""
    if not chars:
        return []
    heights = [c["y1"] - c["y0"] for c in chars if c["y1"] - c["y0"] > 0]
    med_h = statistics.median(heights) if heights else 1.0

    ordered = sorted(chars, key=lambda c: -(c["y0"] + c["y1"]) / 2)
    lines: list = []
    cur: list = []
    ref = None
    for c in ordered:
        yc = (c["y0"] + c["y1"]) / 2
        if ref is None or abs(yc - ref) <= 0.5 * med_h:
            cur.append(c)
            ref = yc if ref is None else ref
        else:
            lines.append(cur)
            cur = [c]
            ref = yc
    if cur:
        lines.append(cur)

    out: list = []
    for ln in lines:
        ln_sorted = sorted(ln, key=lambda c: c["x0"])
        text = join_line_glyphs(ln_sorted)
        if not text:
            continue
        hs = [c["y1"] - c["y0"] for c in ln_sorted if c["y1"] - c["y0"] > 0]
        out.append({"h": statistics.median(hs) if hs else med_h, "text": text})
    return out


def segments_to_markdown(lines: list) -> str:
    """Render merged lines to text, promoting visually large lines to headings."""
    if not lines:
        return ""
    body = statistics.median([ln["h"] for ln in lines])

    def level(ln) -> int:
        txt = ln["text"]
        if (
            not DETECT_HEADINGS
            or body <= 0
            or len(txt) > 80
            or len(txt) < MIN_HEADING_LINE_CHARS
        ):
            return 0
        if txt[-1:] in ".。!！?？,，;；:：":
            return 0
        r = ln["h"] / body
        if r >= 2.0:
            return 1
        if r >= 1.6:
            return 2
        if r >= 1.35:
            return 3
        return 0

    levels = [level(ln) for ln in lines]
    if sum(1 for x in levels if x) > max(1, int(0.4 * len(lines))):
        levels = [0] * len(lines)

    out = []
    for ln, lv in zip(lines, levels):
        out.append(("#" * lv + " " + ln["text"]) if lv else ln["text"])
    return "\n".join(out)


def chars_to_layout_markdown(chars: list, scale: float, width: float) -> str:
    blocks: list = []
    for col in filter_reading_columns(chars, scale, width):
        lines = merge_orphan_punctuation_lines(group_lines(col))
        md = segments_to_markdown(lines)
        if md:
            blocks.append(md)
    return "\n".join(blocks)


def _layout_line_stats(text: str) -> tuple:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0, 0, 0
    single = sum(1 for ln in lines if len(ln) <= 2)
    punct_only = sum(
        1 for ln in lines
        if len(ln) <= 4 and re.fullmatch(r"[\s.,;:!?…·\-–—]+", ln)
    )
    return len(lines), single, punct_only


def _layout_garbled_line_fraction(text: str) -> float:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    garbled = 0
    for ln in lines:
        words = ln.split()
        if len(words) >= 6 and sum(1 for w in words if len(w) <= 2) / len(words) > 0.45:
            garbled += 1
    return garbled / len(lines)


def _plain_is_well_formed(plain: str) -> bool:
    plain = (plain or "").strip()
    if not plain:
        return False
    if re.search(r"\[\w+,\s", plain):
        return True
    if plain.count(" . . ") >= 2:
        return True
    words = re.findall(r"\S+", plain)
    if len(words) < 30:
        return False
    avg_len = sum(len(w) for w in words) / len(words)
    return avg_len >= 5.0


def should_prefer_plain(plain: str, layout: str) -> bool:
    """Fall back to plain text when the layout reconstruction looks broken."""
    layout = (layout or "").strip()
    plain = (plain or "").strip()
    if not layout:
        return True
    if not plain:
        return False
    n, single, punct_only = _layout_line_stats(layout)
    if n == 0:
        return True
    if single / n >= 0.18 or punct_only / n >= 0.12:
        return True
    garbled = _layout_garbled_line_fraction(layout)
    if garbled >= 0.20 and _layout_garbled_line_fraction(plain) < 0.08:
        return True
    if re.search(r"\[\w+,\s", plain) and re.search(r"\[\w+\s+\w+\s+\d", layout):
        return True
    for ln in plain.splitlines():
        probe = ln.strip()
        if len(probe) < 24:
            continue
        alnum = "".join(c for c in probe if c.isalnum())[:16]
        if len(alnum) < 12:
            continue
        layout_alnum = "".join(c for c in layout if c.isalnum())
        if alnum not in layout_alnum:
            return True
        break
    return False


def extract_layout_text(page) -> str:
    """Layout-aware extraction: reading order + headings.

    Falls back to plain extraction on any failure so a single odd page never
    breaks the document (mirrors upstream ``_extract_layout_text``).
    """
    try:
        chars, width = page_chars(page)
        if not chars:
            return ""
        heights = [c["y1"] - c["y0"] for c in chars if c["y1"] - c["y0"] > 0]
        scale = (statistics.median(heights) if heights else 1.0) or 1.0
        return chars_to_layout_markdown(chars, scale, width)
    except Exception:  # noqa: BLE001
        logger.debug("layout extraction failed; using plain text", exc_info=True)
        return page_text(page)