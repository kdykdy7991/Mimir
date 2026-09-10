# SPDX-License-Identifier: MIT
#
# New local parser for SKDY DocReader (Phase 6, format #4: legacy DOC/XLS/PPT).
#
# These are OLE2 Compound Document binary formats, unlike the OOXML (ZIP)
# DOCX/XLSX/PPTX handled elsewhere. There is NO dependency-free stdlib path to
# reliably extract their text, and this offline environment has neither
# LibreOffice / catdoc / antiword nor python-docx / openpyxl / xlrd / olefile.
# Per the plan's escape hatch ("无专有依赖则走 opendataloader 型后端"), these
# formats are registered as an availability-gated backend: advertised as
# unavailable with a clear reason until a working local converter (e.g. an
# OpenDataLoader-style JVM tool) is installed, and ``parse`` refuses to fabricate
# output rather than return an empty placeholder marked as success.
"""Legacy DOC/XLS/PPT parser (availability-gated; no local converter offline)."""

from __future__ import annotations

import logging

from docreader.models.document import Document
from docreader.parser.base_parser import BaseParser
from docreader.parser.opendataloader_parser import _java_available, _package_available

logger = logging.getLogger(__name__)


def legacy_office_available() -> tuple[bool, str]:
    """Availability probe: Java + a local OOXML/legacy converter package."""
    ok, msg = _java_available()
    if not ok:
        return ok, "legacy office (doc/xls/ppt): " + msg
    ok, msg = _package_available()
    if not ok:
        return ok, "legacy office (doc/xls/ppt): " + msg
    return True, ""


class LegacyOfficeParser(BaseParser):
    """Parse a legacy Office binary (doc/xls/ppt) if a local converter exists.

    Raises a documented error when the converter is unavailable — the caller
    must never receive an empty placeholder presented as a successful parse
    (mirrors Phase 5's "no fake success" rule). The ODL package is PDF-centric,
    so wiring a real generic converter is a deployment-time follow-up that
    belongs where the dependency is present.
    """

    def parse_into_text(self, content: bytes) -> Document:
        ok, reason = legacy_office_available()
        if not ok:
            raise ValueError(f"legacy office parsing unavailable: {reason}")
        # No offline converter this environment: this path is reached only on a
        # host that has one; actual conversion wiring is a deployment follow-up.
        raise ValueError(
            "legacy office conversion backend not wired for this build; "
            "install a local converter (JVM OpenDataLoader-style tooling) "
            "to enable doc/xls/ppt parsing",
        )