# WeKnora DocReader migration provenance & source manifest

> Format per plan §6 / §15. All source paths are relative to
> `/home/hello/workspace/WeKnora` at the **fixed** reference commit
> `3e6010e7cd3937f289cc1dbadc829e71eb1163f4`.
>
> License: WeKnora is MIT (Copyright (C) 2025 Tencent) with a
> `THIRD_PARTY_NOTICES.md` / `licenses/` covering Apache-2.0 components
> (paddle-1.1.15, playwright-1.56.0, grpc-health-7.5.0). One docreader
> module carries an InfiniFlow Apache-2.0 header (see below).
>
> Every migrated file must keep its original header and be attributed here.
> Upstream sync commits must reference this source commit in the message.

## Migration-mode legend (§6)

- **直接迁移** — copy with only package path / logging / copyright fixes; keep algorithm & tests.
- **基本迁移** — contract/style-level adaptation; keep structure & tests.
- **重点迁移** — keep core algorithm & tests; adapt settings/output/config/coupling.
- **裁剪迁移** — keep relevant subset; **delete** cloud/paid/out-of-scope paths.
- **契约迁移** — regenerate code from the `.proto`, drop Go package coupling.
- **移栽参考** — study-only; reimplement semantics without copying wholesale.
- **延后** — explicitly deferred, out of scope this round.

## Parser-layer manifest

| Upstream file | Mode | Keep | Adapt/Delete | Tests to port |
| --- | --- | --- | --- | --- |
| `docreader/models/document.py` | 参考重写 | Markdown/images/metadata concepts | become `src/document_parser/types.py` (`ParsedDocument`/`ParsedImage`); drop legacy Chunk | … |
| `docreader/parser/base_parser.py` | 基本迁移 | bytes→Document contract | package path, exceptions, logging | `test_parser_routing.py` |
| `docreader/parser/chain_parser.py` | 基本迁移 | FirstParser / PipelineParser | must NOT swallow final error; return attempt chain | `test_parser_routing.py` |
| `docreader/parser/concurrency.py` | 直接迁移 | process-local BoundedSemaphore | wire project settings/metrics | `test_parser_concurrency.py` |
| `docreader/parser/parser.py` | 基本迁移 | Facade; DOC/DOCX magic fix | request type, error codes, trace metadata | `test_parser_routing.py` |
| `docreader/parser/registry.py` | 裁剪迁移 | engine registration/format routing/fallback | remove cloud engines; avoid eagerly importing heavy optional deps | `test_parser_routing.py` |
| `docreader/parser/pdf_parser.py` | 重点迁移 | page routing, multi-column, title, noise clean, scanned/image handling | settings read, output model, logs; KEEP algorithm tests | `test_pdf_router.py`, `test_pdf_embedded_images.py` |
| `docreader/parser/opendataloader_parser.py` | 裁剪迁移 | local ODL, Markdown, images, low-text fallback | remove MinerU params & remote Hybrid URL; local address only (see `test_ssrf.py`) | `test_opendataloader_parser.py`, relevant `test_ssrf.py` |
| `docreader/parser/docx_parser.py` | 重点迁移 | paragraphs/tables/images + order | config, process pool, output model | `test_docx_tables.py` |
| `docreader/parser/docx_merge.py` | 直接迁移 | vertical merged-cell fill | package path & copyright | `test_docx_merge.py` |
| `docreader/parser/doc_parser.py` | 裁剪迁移 | local antiword/LibreOffice | delete dead compat paths; hard timeouts | — |
| `docreader/parser/excel_parser.py` | 重点迁移 | sheets, tables, merged cells | unified Markdown output; row/col & size caps | `test_excel_parser.py` |
| `docreader/parser/xlsx_merge.py` | 直接迁移 | merged fill | security caps, package path | `test_excel_parser.py` |
| `docreader/parser/xlsx_repair.py` | 直接迁移 | ZIP repair | security caps, package path | `test_excel_parser.py` |
| `docreader/parser/markitdown_parser.py` | 裁剪迁移 | Office/PPT/CSV fallback | concurrency caps, output model, **disable network ability** | — |
| `docreader/parser/ppt_convert.py` | 基本迁移 | LibreOffice convert | subprocess timeout, temp dir, filename sanitize | `test_ppt_convert.py` |
| `docreader/parser/pptx_media.py` | 基本迁移 | media extraction | subprocess timeout, temp dir, filename sanitize | `test_ppt_convert.py` |
| `docreader/parser/markdown_parser.py` | 基本迁移 | image refs + Markdown normalization | forbid downloading unauthorized remote images | `test_markdown_table_util.py` |
| `docreader/parser/html_parser.py` | 基本迁移 | HTML→Markdown | size caps, script/style clean | `test_html_parser.py` |
| `docreader/parser/mhtml_parser.py` | 基本迁移 | main content, embedded images, links | external-link policy & resource caps | `test_mhtml_parser.py` |
| `docreader/parser/epub_parser.py` | 基本迁移 | chapter order, images, links | ZIP-bomb guard | `test_epub_parser.py` |
| `docreader/parser/xmind_parser.py` | 基本迁移 | sheets, hierarchy, notes | ZIP entry/size/encryption checks | `test_xmind_parser.py` |
| `docreader/parser/image_parser.py` | 基本迁移 | original image as parse asset | hand to main-service VLM; NO synthetic text | — |
| `docreader/parser/web_parser.py` | **延后** | — | NOT opened this round (Playwright/SSRF scope) | `test_web_parser.py` not ported yet |

## Service / proto manifest

| Upstream file | Mode | Keep | Adapt/Delete |
| --- | --- | --- | --- |
| `docreader/proto/docreader.proto` | 契约迁移 | ReadStream, ListEngines messages | regenerate Python from `.proto` |
| `docreader/proto/docreader_pb2.py` / `*_pb2_grpc.py` | 契约迁移 | generated stubs | regenerate; drop Go `.pb.go` |
| `docreader/main.py` | 裁剪迁移 | gRPC, health, streaming images, request_id | auth, config, error structure, shutdown flow |
| `docreader/config.py` | 参考重写 | config surface | become `services/docreader/docreader/config.py` with project defaults |
| `docreader/client/*` (Go) | **排除** | — | Go DocReader client is out of scope (§3.2) |
| `docreader/splitter/*` | **排除** | — | old splitter no longer owns production chunking (§3.2) |

## Utils manifest

| Upstream file | Mode | Notes |
| --- | --- | --- |
| `docreader/utils/__init__.py` | 契约迁移 | **InfiniFlow Apache-2.0 header** — must preserve header + Apache-2.0 text on migration |
| `docreader/utils/endecode.py` | 裁剪迁移 | only what the ported parsers need |
| `docreader/utils/request.py` | 裁剪迁移 | remote access is largely excluded; only local needs |
| `docreader/utils/split.py` | 裁剪迁移 | only used helpers |
| `docreader/utils/ssrf.py` | 裁剪迁移 | keep **local-endpoint** restriction cases; remote is out of scope |
| `docreader/utils/tempfile.py` | 裁剪迁移 | safe temp path helper |

## Third-party runtimes referenced upstream (recorded for TODO, not migrated now)

- OpenDataLoader PDF (Apache-2.0, requires Java 11+).
- LibreOffice / antiword (subprocess converters).
- PDFium/pymupdf (already a project dependency).
- grpc / grpcio (new dependency for `services/docreader/`).

## Usage requirement

- Never scatter an upstream file into an existing `PdfLoader` without the
  source path + commit here and in the migrated file (plan §6.1).
- Do not edit `upstream/weknora` files to smuggle project logic.
- No remote endpoint / API key / cloud provider as defaults.