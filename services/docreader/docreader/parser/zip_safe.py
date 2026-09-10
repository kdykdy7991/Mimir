# SPDX-License-Identifier: MIT
#
# ZIP-bomb guard for the dependency-free OOXML/EPUB/XMind parsers (Phase 6, hardening).
# DOCX/XLSX/PPTX/EPUB/XMind are ZIP archives pulled from untrusted uploads; without
# entry-count / size / ratio limits an attacker-controlled archive can exhaust memory.
"""Shared ZIP safety limits for docreader's zip-backed parsers."""

from __future__ import annotations

import io
import zipfile

MAX_ENTRIES = 10_000
MAX_ENTRY_SIZE = 100 * 1024 * 1024  # 100 MB per uncompressed entry
MAX_TOTAL_SIZE = 500 * 1024 * 1024  # 500 MB total uncompressed
MAX_COMPRESSION_RATIO = 200          # uncompressed/compressed guard


class ZipSafetyError(ValueError):
    """Raised when an archive exceeds the ZIP-bomb safety limits."""


def check_zip_archive(
    zf: zipfile.ZipFile,
    *,
    max_entries: int = MAX_ENTRIES,
    max_entry_size: int = MAX_ENTRY_SIZE,
    max_total_size: int = MAX_TOTAL_SIZE,
    max_ratio: float = MAX_COMPRESSION_RATIO,
) -> None:
    """Validate member count / per-entry size / total size / compression ratio.

    Raises :class:`ZipSafetyError` on any violation. Cheap: iterates
    ``ZipInfo`` metadata without reading archive payloads first.
    """
    infos = zf.infolist()
    if len(infos) > max_entries:
        raise ZipSafetyError(
            f"archive has {len(infos)} entries (limit {max_entries})",
        )
    total = 0
    for info in infos:
        if info.file_size > max_entry_size:
            raise ZipSafetyError(
                f"entry {info.filename!r} uncompressed size "
                f"{info.file_size} exceeds {max_entry_size}",
            )
        total += info.file_size
        if total > max_total_size:
            raise ZipSafetyError(
                f"archive total uncompressed size {total} exceeds {max_total_size}",
            )
        if info.compress_size and max_ratio and info.file_size > info.compress_size * max_ratio:
            raise ZipSafetyError(
                f"entry {info.filename!r} compression ratio "
                f"{info.file_size}/{info.compress_size} exceeds {max_ratio:g}",
            )


def open_safe_zip(content: bytes, **limits) -> zipfile.ZipFile:
    """Open ``content`` as a zip and enforce the safety limits immediately."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a valid zip archive: {exc}") from exc
    try:
        check_zip_archive(zf, **limits)
    except Exception:
        zf.close()
        raise
    return zf


def read_member(
    zf: zipfile.ZipFile,
    name: str,
    *,
    max_size: int = MAX_ENTRY_SIZE,
) -> bytes:
    """Read a member raising if it blows the per-entry size cap."""
    info = zf.getinfo(name)
    if info.file_size > max_size:
        raise ZipSafetyError(
            f"entry {name!r} uncompressed size {info.file_size} exceeds {max_size}",
        )
    data = zf.read(name)
    if len(data) > max_size:
        raise ZipSafetyError(f"entry {name!r} expanded beyond {max_size} bytes")
    return data