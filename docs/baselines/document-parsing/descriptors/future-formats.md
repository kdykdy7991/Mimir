# Future-format expected-structure descriptors

> These describe what the *migrated* parser must preserve for formats that are
> **not** part of the Phase-0 baseline (the legacy chain only parses PDF +
> Markdown). Real fixture binaries are added when each format is migrated in
> Phase 6; these notes lock the expected structure so the acceptance gates are
> defined up front.

Conventions: "expected structure" below is about what enters the
`ParsedDocument.markdown` / images / metadata contract
(`src/document_parser/types.py`), not about the bytes.

---

## DOCX (resolve phase: Phase 6.1)

- Preserve **reading order** of paragraphs, tables, and images as authored.
- Tables: keep row/column layout; merged cells that are losslessly representable
  in GFM should be flattened, and `rowspan/colspan`-only tables should be kept
  as cleared HTML (no style/presentation attributes).
- Images: extract bytes with a stable filename; no base64 into logs/metadata.
- Failure: corrupted container → stable error, no partial text chain relying on
  hidden state.
- Reference (WeKnora): `docreader/parser/docx_parser.py` + `docx_merge.py`.
  Expected upstream regressions to adapt: `test_docx_tables.py`, `test_docx_merge.py`.

## XLSX / CSV (Phase 6.2)

- Each worksheet → its own GFM/HTML table in document order.
- Merged cells filled down as in WeKnora `xlsx_merge.py`.
- Guardrails: cap rows/columns/file size; reject encrypted/unsafe ZIP entries.
- Reference: `excel_parser.py`, `xlsx_merge.py`, `xlsx_repair.py`.
  Expected upstream regressions: `test_excel_parser.py`.

## PPTX (Phase 6.3)

- Slide order preserved; extract text plus slide images/media.
- LibreOffice-converted fallback carries a subprocess timeout.
- Reference: `markitdown_parser.py` (fallback), `ppt_convert.py`, `pptx_media.py`.
  Expected upstream regression: `test_ppt_convert.py`.

## Legacy DOC / XLS / PPT (Phase 6.4)

- Local conversion only (antiword / LibreOffice) with timeout, isolated working
  dir, and exit-code checks. No remote services.
- Reference: `doc_parser.py`.

## TXT / HTML / MHTML (Phase 6.5)

- TXT: closes the current MIME/extension white-list inconsistency
  (`text/plain` allowed but `.txt` missing in `src/web_api/settings.py`).
- HTML/MHTML: strip scripts/styles, cap size, default no remote-image download.
- Reference: `markdown_parser.py`, `html_parser.py`, `mhtml_parser.py`.
  Expected upstream regressions: `test_mhtml_parser.py`, `test_html_parser.py`.

## EPUB / XMind (Phase 6.6)

- EPUB: chapter order, links, images; ZIP-bomb guards.
- XMind: sheets, hierarchy, notes; ZIP entry-count/size/encryption checks.
- Reference: `epub_parser.py`, `xmind_parser.py`.
  Expected upstream regressions: `test_epub_parser.py`, `test_xmind_parser.py`.

## Image formats / image tables / OCR (Phase 6.7 + Phase 5)

- Image files parse to an image asset; **no synthetic text** is fabricated.
  OCR/table reconstruction is a Phase-5 local-VLM responsibility
  (`supports_vision` capability, local Qwen3.8 27B only).
- Reference: `image_parser.py`.