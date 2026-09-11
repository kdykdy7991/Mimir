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