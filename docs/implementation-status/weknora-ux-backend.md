# 实施状态 — WeKnora 可用性优化：后端任务书

> 任务书：`docs/plan-2026-09-11-weknora-ux-backend-tasks.md`
> 目标分支：`feature/weknora-inspired-optimizations`
> 实施规则：严格执行 B0 → B1 → B2 → B3 → B4 依赖顺序；每个任务完整闭环并独立提交；
> 不实施前端；不覆盖用户已有工作区改动；不使用 `reset --hard` / `checkout --` / `clean`。

---

## 工作区基线（2026-09-11）

**分支**：`feature/weknora-inspired-optimizations`（正确）。

**用户已有未提交改动（必须保留，不得混入后端提交）**：

- `README.md`
- `config/settings.yaml`
- `deploy/mcp/Dockerfile`
- `docker-compose.yml`
- `docs/mcp-integration.md`
- `web/.env.example`、`web/README.md`
- `web/src/features/knowledge/collections-view.tsx`、`document-table.tsx`
- `web/src/features/system/mcp-keys-view.tsx`
- 未跟踪：`docs/mcp-server-connection.md`、`docs/plan-2026-09-11-weknora-ux-backend-tasks.md`、
  `docs/plan-2026-09-11-weknora-ux-frontend-tasks.md`、`web/src/.../*.test.tsx`、`web/src/.../*.test.ts`

> 上述文件与后端 B0–B4 目标前端/部署/文档相关；后端实现不会修改它们。

**既有基线问题（非本次实现造成，需修复才能通过 B0 门禁）**：

- `tests/contract/test_openapi_snapshot.py` 在 HEAD 即失败：提交的
  `docs/openapi/openapi.v0.2.json` 未包含已合入代码的 `/internal/mcp/v1/*` 四组内部只读路由
  （live `app.openapi()` = 25 个 path，快照 = 21 个 path）。需重新生成快照修复。
- 复现命令：`.venv/bin/python -m scripts.export_openapi --check`（FAIL）。

**架构实测结论（子代理体系化勘察 + 人工校对）**：

- 文档队列（即“文档”）存在于 `data/db/ingestion_history.db`（`SQLiteIntegrityChecker`，
  key = `(collection, file_hash)`）；Chunk 位于 **ChromaDB**（`ChromaStore`），非 SQLite。
- Web API 元数据使用 `WebApiDB`（`src/application/services/web_store.py`），SQLite + WAL，无 schema 版本机制，
  建表在 `_ensure_schema` 的 `executescript` 块，演进用 `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`。
- Chunk 稳定排序逻辑现存在于 MCP `in_process.py::_chunk_sort_key`（chunk_index → 旧 ID 索引 → ID），
  以及 `mappers.py` `to_document_detail` 的按 `chunk_index` 排序。B1 需复用/下沉，不得复制两套规则。
- `TaskStatus` 已含 `cancelled`/`skipped`/`retrying`-absent 设计；`attempt` 已存在；无 `mark_cancelled`/retry。
- Trace 存 `data/traces/traces.jsonl`（TraceStore 内存索引 + SQLite `traces` 表 + JSONL）；无 list 分页方法。
- MCP Key 仅存 `sha256(secret)` 摘要；完整 Key 仅创建/轮换时返回一次。`MCP_INTERNAL_API_KEY` → `settings.mcp_server.api_key`。
- 服务端 MCP 匿名 `/health`（`{"status":"ok","transport":"streamable-http"}`）；公开客户端 URL = `mcp_server.rag_api_base_url`。

---

<!-- 每个任务完成时追加相应小节 -->

---

## B2.4 文件夹 CRUD 与文档移动接口 — ✔ 完成

**状态**：通过。

**实际修改文件**：

- 新增 `src/web_api/schemas/folders.py`（`Folder`/`FolderCreateRequest`/`FolderRenameRequest`/
  `FolderMoveRequest`/`FolderListResponse`/`DocumentFolderUpdateRequest`/`DocumentFolderResponse`）。
- 新增 `src/web_api/routers/folders.py`。
- `src/web_api/app.py` — 注册 folders 路由。
- 新增 `tests/integration/test_web_api_folders.py`；`tests/unit/test_ux_api_contracts.py` 新增
  `test_b24_folders_contract`；`docs/openapi/openapi.v0.2.json`（重新生成）。

**数据库/API/兼容性决策**：

- 端点：`GET/POST /collections/{id}/folders`、`PATCH .../{folder_id}`（重命名）、
  `POST .../{folder_id}/move`（重挂载，body `parent_id`，null=移到根）、
  `DELETE .../{folder_id}`（删除并上移子节点/文档）、`PUT /documents/{id}/folder`（设置/清除）。
- 文件夹仅逻辑目录，不移动文件物理路径。跨 collection 文件夹 → 404 `FOLDER_NOT_FOUND`（防枚举）。
- 文档移动文件夹必须属于文档所在 collection，否则 400 且不改写；文档不存在 → 404。
- 同名冲突 → 409；未知父/深度越界/环移动 → 400。
- `Folder.document_count` = 该文件夹直属文档数。

**新增测试**（`tests/integration/test_web_api_folders.py`，9 项）：CRUD 流程、同名 409、
未知父 400、环移动 400、跨 collection 404、未知 collection 404、文档设置/清除文件夹、
跨 collection 文档移动 400、未知文档 404。

**执行命令与结果**：

- `python -m scripts.export_openapi` → 34 paths, 67 schemas
- `python -m pytest tests/integration/test_web_api_folders.py tests/unit/application/test_folders_store.py tests/unit/test_ux_api_contracts.py tests/contract/test_openapi_snapshot.py tests/integration/test_web_api_endpoints.py -q` → 63 passed

**git diff --check**：通过。

**提交哈希**：B2.4 提交见当前小节末尾。

**修正（B2.3/24 语义对齐）**：任务书 B2.3 明确"删除非空文件夹返回 409，第一版不做递归强删"。
先前 B2.3/B2.4 采用"删除并重挂载子节点/文档"语义，与任务书不符；已在后续
`fix(folders): reject deleting non-empty folders` 提交中改为：非空文件夹删除 → 409 `CONFLICT`
（不改写任何数据），空文件夹删除 → 204。新增 `FolderNotEmptyError`（app 层），router 映射为 409。

**遗留问题**：无新遗留。

**下一任务**：B2.5 文档组合筛选。

---

## B2.3 文件夹数据模型和迁移 — ✔ 完成

**状态**：通过。

**实际修改文件**：

- `src/application/services/web_store.py` — `document_folders` / `document_placements` 建表
  （`(collection_id, parent_id, normalized_name)` 唯一；平铺树，根用 parent_id NULL）；
  `MAX_FOLDER_DEPTH=5`、`normalize_folder_name`；新增 `create_folder`/`get_folder`/
  `list_folders`/`rename_folder`/`move_folder`/`delete_folder`/`move_document`/
  `document_placement`/`documents_by_folder`/`count_documents_in_folder`。
- 新增 `tests/unit/application/test_folders_store.py`。

**数据库/API/兼容性决策**：

- 文件夹纯逻辑目录，移动原始文件物理路径；根=parent_id NULL，无数值型根行。
- collection 内同父下规范化名唯一；深度上限 5；移动做环检测（禁止移入自身后代）并重算子树深度。
- 删除文件夹：子文件夹与文档上移父文件夹，返回 `{reparented_folders, reparented_documents}`，不删除文档。
- 文档→文件夹放置存 `document_placements(document_id PK, folder_id, collection_id)`，folder_id NULL=根。
- `WebApiDB.__init__` 幂等建表，既有库自动补建。

**新增测试**：`tests/unit/application/test_folders_store.py`（12 项）：迁移建表、根/嵌套创建、
同父唯一名、未知父拒绝、深度上限、移动环阻止、移动重算深度、删除重挂载子文件夹+文档计数、
按文件夹查询与计数、放置 upsert/清理、list 稳定顺序、名称长度校验。

**执行命令与结果**：

- `python -m pytest tests/unit/application/test_folders_store.py tests/unit/application/test_tags_store.py -q` → 24 passed
- `python -m pytest tests/unit/application/ -q` → 87 passed（既有 store/task/service 无回归）

**git diff --check**：通过。

**提交哈希**：B2.3 提交见当前小节末尾。

**遗留问题**：无新遗留。

**下一任务**：B2.4 文件夹 CRUD 与文档移动接口。

---

## B2.2 标签 CRUD 与文档绑定接口 — ✔ 完成

**状态**：通过。

**实际修改文件**：

- 新增 `src/web_api/schemas/tags.py`（`Tag`/`TagCreateRequest`/`TagUpdateRequest`/
  `TagListResponse`/`DocumentTagsUpdateRequest`/`DocumentTagsResponse`）。
- 新增 `src/web_api/routers/tags.py` — `GET/POST /collections/{id}/tags`、
  `PATCH/DELETE /collections/{id}/tags/{tag_id}`、`PUT /documents/{id}/tags`。
- `src/web_api/app.py` — 注册 tags 路由（/api/v1 前缀）。
- `src/web_api/errors.py` — 新增 `TagNotFoundError`（404）、`FolderNotFoundError`（为 B2.3-24 预留）。
- 新增 `tests/integration/test_web_api_tags.py`；`tests/unit/test_ux_api_contracts.py` 新增
  `test_b22_tags_contract`；`docs/openapi/openapi.v0.2.json`（重新生成）。

**数据库/API/兼容性决策**：

- 所有标签访问 collection 作用域：不在指定 collection 的 tag → 404 `TAG_NOT_FOUND`（防枚举，不泄露跨库存在性）。
- 文档标签绑定为全量替换、事务性；跨 collection/未知 tag → 400，且不做部分写入；重复 id 去重。
- 文档不存在 → 404 `DOCUMENT_NOT_FOUND`；重复规范化名 → 409 `CONFLICT`；非法颜色/名长 → 400。
- `Tag.name` 1–64；`color` 受控 token。

**新增测试**（`tests/integration/test_web_api_tags.py`，9 项）：CRUD 全流程、重复名 409、非法颜色 400、
不存在 collection 404、跨 collection patch/delete 404（防枚举）、全量替换、重复 id 去重、
跨 collection 绑定 400（无部分写入）、未知文档 404。

**执行命令与结果**：

- `python -m scripts.export_openapi` → 30 paths, 60 schemas
- `python -m pytest tests/unit/test_ux_api_contracts.py tests/contract/test_openapi_snapshot.py tests/integration/test_web_api_tags.py tests/unit/application/test_tags_store.py tests/integration/test_web_api_endpoints.py -q` → 62 passed

**git diff --check**：通过。

**提交哈希**：B2.2 提交见当前小节末尾。

**遗留问题**：无新遗留。

**下一任务**：B2.3 文件夹数据模型和迁移。

---

## B2.1 标签数据模型和迁移 — ✔ 完成

**状态**：通过。

**实际修改文件**：

- `src/application/services/web_store.py` — `document_tags` / `document_tag_links` 建表
  （executescript + 唯一索引 `(collection_id, normalized_name)`）；新增 `TAG_COLORS`、
  `normalize_tag_name`；新增标签存取方法（`create_tag`/`get_tag`/`list_tags`/`update_tag`/
  `delete_tag`/`set_document_tags`/`document_tag_ids`/`count_tag_links`）。
- 新增 `tests/unit/application/test_tags_store.py`。

**数据库/API/兼容性决策**：

- `document_tags(id, collection_id, name, normalized_name, color, created_at, updated_at)`；
  `document_tag_links(document_id, tag_id, created_at)`，PK=(document_id, tag_id)。
- collection 内规范化名唯一（strip + casefold），违者抛 `sqlite3.IntegrityError`（Router 层映射 409）。
- 标签名去空白后 1–64；颜色受控 token（`grey|blue|green|red|purple|amber`），拒绝任意 CSS；否则 `ValueError`。
- 删除标签仅级联删除关联（`document_tag_links`），不删除文档。
- `WebApiDB.__init__` 幂等建表，既有库下次启动自动补建。

**新增测试**：`tests/unit/application/test_tags_store.py`（12 项）：迁移建表、创建/读取、collection 内唯一、
跨 collection 同名允许、名称长度校验、颜色 token 校验、Unicode 名称、list 顺序与作用域、
绑定全量替换与级联删除、未知删除、链接计数、改名重算 normalized。

**执行命令与结果**：

- `python -m pytest tests/unit/application/test_tags_store.py -q` → 12 passed
- `python -m pytest tests/unit/application/ -q` → 75 passed（既有 store/task/service 无回归）

**git diff --check**：通过。

**提交哈希**：B2.1 提交见当前小节末尾。

**遗留问题**：无新遗留。`document_folders` 建表将在 B2.3 加入（本任务保持仅标签，未夹带）。

**下一任务**：B2.2 标签 CRUD 与文档绑定接口。

---

## B1.3 原文件定位契约 — ✔ 完成

**状态**：通过（含 B1 项目门禁合集通过）。

**实际修改文件**：

- 新增 `tests/unit/test_chunk_order.py` — 对 `build_source_locator` 各类映射与降级、
  `page_number_of`/`heading_of`/`chunk_id_of`/`chunk_sort_key`/`stable_order_chunks`
  的专项单元测试。

**说明**：`source_locator` 归一化逻辑（`src/ingestion/chunk_order.py::build_source_locator`）
已在 B1.1 落地并为 B1.1 响应所复用；本任务补齐其契约测试与降级覆盖，未改解析器、
未改预览接口（预览仍走既有受控 `GET /documents/{id}/preview`，不暴露磁盘路径）。

**数据库/API/兼容性决策**：

- PDF → 1-based `pdf_page`（缺页/页码 <1 → `none`）；图片（扩展名或
  `image_ocr`/`image_caption`）→ 整体图片 `image`（`page=null`）；
  DOCX/Markdown/TXT 在存在 heading（元数据或正文 Markdown 标题）时 → `section`；
  无可靠定位元数据 → `none`，禁止猜测。未知扩展名一律 `none`。

**新增测试**：`tests/unit/test_chunk_order.py`（14 项）：PDF 1-based 页码、缺页/零负页降级、
图片扩展名、图片内容类型、docx/txt heading、正文标题 section、section 扩展名无 heading 降级、
未知扩展名 none、禁止猜测、页码归一化、heading 推导、chunk id 回退、排序 key、稳定顺序。

**执行命令与结果**：

- `python -m pytest tests/unit/test_chunk_order.py tests/integration/test_web_api_chunks.py tests/unit/test_ux_api_contracts.py tests/contract/test_openapi_snapshot.py tests/unit/test_readonly_client_chunks.py tests/unit/test_get_document_chunks.py -q` → 63 passed

**git diff --check**：通过。

**提交哈希**：B1.3 提交见当前小节末尾。

**遗留问题**：无新遗留（本任务未改 OpenAPI schema，快照保持 B1.2 版本有效）。

**下一任务**：B2.1 标签数据模型和迁移。

---

## B1.2 文档 Chunk 分页、搜索和过滤 — ✔ 完成

**状态**：通过（含 B1 项目门禁合集通过）。

**实际修改文件**：

- `src/web_api/settings.py` — 新增 `chunk_page_size_default=50` / `chunk_page_size_max=100`
  （env：`WEB_API_CHUNK_PAGE_SIZE_DEFAULT/MAX`）。
- `src/web_api/schemas/documents.py` — 新增 `ChunkListItem`、`DocumentChunkListResponse`。
- `src/web_api/routers/documents.py` — 新增 `GET /documents/{id}/chunks`（page/page_size/q/content_type/page_number）。
- `tests/integration/test_web_api_chunks.py`（B1.2 测试 12 项）、`tests/unit/test_ux_api_contracts.py`
  （新增 `test_b12_chunk_list_contract`）、`docs/openapi/openapi.v0.2.json`（重新生成）。

**数据库/API/兼容性决策**：

- 分页统一 `page/page_size`：默认 50、最大 100（`page_size` 越界 → 422）。
- 稳定排序沿用与 MCP 共享的 `stable_order_chunks`；分页在稳定排序结果上切窗。
- `q` 去首尾空白后做大小写不敏感字面量包含匹配（无全文检索引擎）；>200 字符 → 400 `BAD_REQUEST`。
- `content_type` 白名单 `text|table|image_ocr|image_caption`，非法类型 → 422。
- `page_number` 是原文页码筛选（非分页页码），`>=1`；缺页码 Chunk 被过滤。
- 列表项返回可预览摘要（`text_preview`），全文由 B1.1 提供；文档详情 `chunks` 兼容字段保留不废弃。

**新增测试**（B1.2，12 项）：默认页大小/总数/has_next、中/尾页、越界页为空、中文搜索、字面量搜索大小写、
content_type 过滤、page_number 源页码过滤、组合过滤、超长 q=400、非法 content_type=422、
page_size 越界=422、未知文档 404、旧数据稳定顺序。

**执行命令与结果**：

- `python -m scripts.export_openapi` → 27 paths, 54 schemas
- `python -m pytest tests/integration/test_web_api_chunks.py tests/unit/test_ux_api_contracts.py tests/contract/test_openapi_snapshot.py tests/integration/test_web_api_endpoints.py tests/unit/test_readonly_client_chunks.py tests/unit/test_get_document_chunks.py tests/unit/test_readonly_client_inprocess_services.py -q` → 75 passed

**git diff --check**：通过。

**提交哈希**：B1.2 提交见当前小节末尾。

**遗留问题**：无新遗留。

**下一任务**：B1.3 原文件定位契约（`source_locator` 归一化已在 B1.1 落地，本任务补齐契约测试与声明）。

---

## B1.1 单 Chunk 详情读取 — ✔ 完成

**状态**：通过（含 B1 阶段相关门禁部分，门禁合集见下方）。

**实际修改文件**：

- 新增 `src/ingestion/chunk_order.py` — 下沉 Chunk 稳定排序（`chunk_sort_key`/
  `stable_order_chunks`）与 `source_locator` 归一化（`build_source_locator`）、
  `page_number_of`/`heading_of`/`chunk_id_of`；供 MCP 与 Web 单源复用。
- `src/mcp_server/clients/in_process.py` — `_chunk_sort_key`/`_ordered_chunks`/
  `_page_number` 改委托共享 helper，删除重复排序规则。
- `src/web_api/schemas/documents.py` — 新增 `SourceLocator`、`DocumentChunkDetail`。
- `src/web_api/errors.py` — 新增 `ChunkNotFoundError`（404 `CHUNK_NOT_FOUND`）。
- `src/web_api/routers/documents.py` — 新增 `GET /documents/{id}/chunks/{chunk_id}`。
- `tests/integration/test_web_api_chunks.py`（新增）、`tests/unit/test_ux_api_contracts.py`、
  `docs/openapi/openapi.v0.2.json`（重新生成）。

**数据库/API/兼容性决策**：

- 先按文档归属（collection, source_path）解析并校验 collection，再读 Chunk；错文档 Chunk 返回
  `404 CHUNK_NOT_FOUND`，未知文档 `404 DOCUMENT_NOT_FOUND`（防枚举语义不变）。
- 相邻关系用与 MCP `get_document_chunks` 同一 `stable_order_chunks` 排序（chunk_index → 旧 ID 索引 → ID），
  不复制第二套规则。
- 缺页码/标题/相邻项返回 `null`；`page<1` 归一为 `null`。
- `source_locator.kind ∈ {pdf_page, image, section, none}`；image 定位整图故 `page=null`；
  禁止猜测，无可靠定位 → `none`。

**新增测试**（`tests/integration/test_web_api_chunks.py`，7 项）：正常详情、首尾相邻、
旧数据无 chunk_index 排序、错文档 Chunk 404、未知文档 404、缺定位信息降级、image/section/none 定位映射。
`tests/unit/test_ux_api_contracts.py` 新增 `test_b11_chunk_detail_contract`。

**执行命令与结果**：

- `python -m scripts.export_openapi` → 26 paths, 52 schemas
- `python -m pytest tests/unit/test_readonly_client_chunks.py tests/unit/test_get_document_chunks.py tests/unit/test_readonly_client_inprocess_services.py -q` → 15 passed（MCP Chunk 回归）
- B1 门禁（B1 相关测试 + MCP Chunk 回归 + 文档详情回归 + OpenAPI 快照）：
  `python -m pytest tests/integration/test_web_api_chunks.py tests/unit/test_ux_api_contracts.py tests/contract/test_openapi_snapshot.py tests/integration/test_web_api_endpoints.py tests/unit/test_readonly_client_chunks.py tests/unit/test_get_document_chunks.py tests/unit/test_readonly_client_inprocess_services.py tests/unit/test_readonly_client_contracts.py tests/unit/test_readonly_client_document.py tests/unit/test_document_detail_presentation.py -q` → 72 passed

**git diff --check**：通过。

**提交哈希**：B1.1 提交见当前小节末尾。

**遗留问题**：无新遗留；本任务已完成 source_locator 归一化（属 B1.3 契约，先落地于 B1.1 使响应完整）。

**下一任务**：B1.2 文档 Chunk 分页、搜索和过滤。

---

## B0.1 建立 OpenAPI 契约测试 — ✔ 完成

**状态**：通过（含 B0 Phase Gate）。

**实际修改文件**：

- 新增 `tests/unit/test_ux_api_contracts.py` — 冻结新端点分页上限/枚举/错误码，并对现有
  文档、Trace、MCP Key、Task 响应建立兼容断言；`PLANNED_ENDPOINTS` 登记后续 B1–B4 端点；
  采用“后续任务逐个追加断言并转绿”，不整体 xfail。
- `docs/openapi/openapi.v0.2.json`（在独立预备提交中重新生成，修复既有基线漂移——
  补入已合入的 `/internal/mcp/v1/*` 只读路由与 `_QueryBody` schema）。
- 新增 `docs/implementation-status/weknora-ux-backend.md`（本状态文档）。

**数据库/API/兼容性决策**：

- 新后端端点分页统一采用任务书 §4：默认 `page_size=50`、最大 `100`。
- Chunk `content_type` 白名单：`text|table|image_ocr|image_caption`；`source_locator.kind`：
  `pdf_page|image|section|none`。
- 错误信封仍为现有 `{error:{code,message,request_id,details}}`；错误码属
  `APIError` 子类稳定 code，不由 OpenAPI components 暴露。

**新增测试**：`tests/unit/test_ux_api_contracts.py`（11 项）：分页契约冻结、文档契约、
Chunk 摘要契约、Trace 契约、MCP Key 契约、Task 契约、错误信封契约、错误码契约、
游标分页上限、PageInfo 稳定、计划端点注册表。

**执行命令与结果**：

- `python -m scripts.export_openapi --check` → `OK: ... matches live schema (25 paths)`
- `python -m pytest tests/unit/test_ux_api_contracts.py tests/contract/test_openapi_snapshot.py -q` → 13 passed
- B0 Phase Gate：`python -m pytest tests/contract/test_openapi_snapshot.py tests/integration/test_web_api_endpoints.py tests/integration/test_web_api_traces.py tests/integration/test_web_api_mcp_keys.py -q` → 33 passed

**git diff --check**：通过（无空白错误）。

**提交哈希**：

- `534ccdc` — `chore(openapi): refresh snapshot ...`（预备：修复基线 OpenAPI 漂移）
- B0.1 提交见当前小节末尾哈希。

**遗留问题 / 环境限制**：

- 既有基线 OpenAPI 快照漂移（internal readonly MCP 路由未入快照）已在预备提交中修复；
  非本次实现造成，已记录并复现。

**下一任务**：B1.1 单 Chunk 详情读取。