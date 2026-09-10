"""ZIP-bomb hardening for the docreader zip-backed parsers (Phase 6 rework).

These archives come from untrusted uploads; the shared ``zip_safe`` guard must
reject excessive entry counts / per-entry sizes / total sizes / compression
ratios before any parser decompresses content.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from docreader.parser.zip_safe import (
    MAX_ENTRIES,
    MAX_ENTRY_SIZE,
    ZipSafetyError,
    check_zip_archive,
    open_safe_zip,
    read_member,
)


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def test_too_many_entries_rejected():
    content = _zip([(f"e{i}.xml", b"x") for i in range(MAX_ENTRIES + 1)])
    with pytest.raises(ZipSafetyError):
        with open_safe_zip(content) as zf:
            check_zip_archive(zf)


def test_oversized_entry_rejected():
    big = b"A" * (MAX_ENTRY_SIZE + 1)
    content = _zip([("huge.bin", big)])
    with pytest.raises(ZipSafetyError):
        with open_safe_zip(content) as zf:
            check_zip_archive(zf)


def test_compression_ratio_guard():
    # Deterministic ratio check independent of the deflate settings: fabricate a
    # ZipInfo whose advertised sizes encode a high compression ratio and mutate
    # the archive's ZipInfo so check_zip_archive sees ratio 1000 (> default 200).
    content = _zip([("bomb.bin", b"seed")])
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        info = zf.infolist()[0]
        info.file_size = 1_000_000
        info.compress_size = 1_000
        with pytest.raises(ZipSafetyError):
            check_zip_archive(zf)
        # below the default 200 but above a tight cap -> still rejected
        info.file_size = 100_000
        info.compress_size = 1_000  # ratio 100
        check_zip_archive(zf, max_ratio=200)  # ok
        with pytest.raises(ZipSafetyError):
            check_zip_archive(zf, max_ratio=50)


def test_read_member_caps_single_entry(tmp_path):
    content = _zip([("a.txt", b"hello")])
    with open_safe_zip(content) as zf:
        assert read_member(zf, "a.txt") == b"hello"
        zf.close()
    # a member advertised as small but expanded beyond the cap — via manual zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("big.txt"), b"x" * 100)
    content = buf.getvalue()
    with open_safe_zip(content) as zf:
        with pytest.raises(ZipSafetyError):
            read_member(zf, "big.txt", max_size=10)
        zf.close()


def test_sane_archive_passes():
    content = _zip([("doc.xml", b"<root/>")])
    with open_safe_zip(content) as zf:
        check_zip_archive(zf)
        assert read_member(zf, "doc.xml") == b"<root/>"
        zf.close()