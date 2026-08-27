# Web API Onboarding (v0.2)

> 状态：v0.2 契约 / 已冻结（M2 批次 3 后）
>
> 目标读者：Web 前端 (A 角色) 与任何需要 mock Web API 的工具
>
> 关联文档：[PRODUCTION_WEB_DEV_SPEC.md](../PRODUCTION_WEB_DEV_SPEC.md) · [TEAM_RESPONSIBILITIES.md](../TEAM_RESPONSIBILITIES.md)

本文档告诉你怎么把前端接上 SKDY RAG Server 的 Web API，并列出当前 v0.2 的硬性约定。任何与本文档不符的实现都视为 bug。

## 1. 启动后端

```bash
# 装依赖（一次性）
python -m pip install -e ".[local]"   # local 段只在选 sentence_transformers/cross_encoder 时需要

# 启 Web API（默认 127.0.0.1:8766）
python -m src.web_api.main
# 或：
uvicorn src.web_api.app:app --host 127.0.0.1 --port 8766 --reload
```

Swagger UI: <http://127.0.0.1:8766/docs>
ReDoc: <http://127.0.0.1:8766/redoc>
OpenAPI JSON: <http://127.0.0.1:8766/openapi.json>

## 2. 关键约定

### 2.1 地址

| 资源 | URL |
|---|---|
| API 根 | `http://127.0.0.1:8766/api/v1` |
| OpenAPI 文档（人类） | `http://127.0.0.1:8766/docs` |
| OpenAPI JSON（类型生成） | `http://127.0.0.1:8766/openapi.json` |
| **检查后的快照** | [`docs/openapi/openapi.v0.2.json`](openapi/openapi.v0.2.json) |

> OpenAPI 快照与运行时一致；CI 有测试守护。重新生成只需 `python -m scripts.export_openapi`。

### 2.2 CORS / 代理

M1 默认 `Access-Control-Allow-Origin: *`（便于本地 Next.js dev 跨域）。生产建议：

- 走同源（Next.js 代理到 FastAPI），**或**
- 反向代理（Nginx / Caddy）在边缘加 CORS 头并去掉 `*`

环境变量覆盖：`WEB_API_CORS_ALLOW_ORIGINS=https://app.example.com`

### 2.3 请求超时

- 默认建议 `60s`（覆盖绝大多数 query + ingestion 列表）。
- 长任务**不**用长连接：先 `POST /collections/{id}/documents` → 拿 `task_id` → 轮询 `GET /tasks/{id}`。
- 环境变量覆盖：`WEB_API_REQUEST_TIMEOUT_SECONDS`

### 2.4 Request ID

每个请求 / 响应都带 `X-Request-ID`：

- 客户端可传（便于关联用户报告），不传则服务端生成 `<unix_ms>-<8 hex>`。
- 4xx / 5xx 响应 body 中也带 `error.request_id`，贴进 issue 比对日志用。

### 2.5 文件上传

| 项 | 约束 |
|---|---|
| Content-Type | `application/pdf`（M1），可通过 `WEB_API_UPLOAD_ALLOWED_MIME` 扩展 |
| 单文件上限 | **30 MB**，可通过 `WEB_API_UPLOAD_MAX_BYTES` 调整 |
| 表单字段名 | `file`（multipart/form-data） |
| 超过大小 | `413 PAYLOAD_TOO_LARGE`，body `details: {size_bytes, max_bytes}` |
| 错类型 | `415 UNSUPPORTED_MEDIA_TYPE`，body `details: {received_content_type, allowed_content_types}` |
| 重复文件 | 由 M2 实现 `409 DUPLICATE_DOCUMENT` 编码；M1 暂时按成功上传 |
| 摄取失败 | `task.status` 转 `failed`，`document.status = failed`，详见 `task-failed.json` |

### 2.6 当前明确不做（避免误判）

- **无登录 / 无用户中心**。
- **无组织 / 工作区 / 多租户**。
- **无 RBAC / 审计后台 / 计费**。
- **无限流**（应用层）—— 走反向代理或单进程信任即可。
- 鉴权错误码 `UNAUTHORIZED` / `FORBIDDEN` 暂未发布（虽然 `HTTPException` 会自动映射），不要在前端业务逻辑里依赖。

> 这些都是 v0.2 的**显式约束**，不是遗漏；如需变更，按 §6 接口变更规则。

## 3. 统一数据约定

### 3.1 ID

- `Collection` / `Document` / `Task` / `Query` / `Trace` ID：**UUID 字符串**。
- `Citation.chunk_id` 和 `CitationImage.id`（即 `image_id`）：**不透明稳定字符串**，前端**不解析、不生成**这两个 ID；只是从后端响应里拿到后原样用作缓存 key、URL 路径段等。
  - `chunk_id` 的格式是 `[A-Za-z0-9_-]{1,128}`（后端索引器生成的 hash）
  - `image_id` 形如 `img-001` 或 `{doc_hash}_{page}_{seq}`（后端图片存储生成的稳定 id）
- 图片 URL 仍是 `/api/v1/images/{image_id}`，使用 `image_id` 即可。

### 3.2 时间

- 所有 `*_at` 字段：`ISO 8601 UTC，毫秒精度 + Z 后缀`。
  例：`"2026-07-31T08:23:11.234Z"`
- `Date.parse(...)` / `new Date(s)` 直接接受。

### 3.3 分页

```json
{
  "items": [...],
  "page_info": {
    "next_cursor": "eyJwYWdlIjoyfQ==",
    "has_more": true
  }
}
```

- 默认 `limit = 20`，硬上限 `limit ≤ 100`。
- `cursor` 是**不透明字符串**，不要解析或构造；只能从上一次响应的 `next_cursor` 原样回传。
- 末页 `next_cursor = null`。

### 3.4 错误响应

```json
{
  "error": {
    "code": "DOCUMENT_NOT_FOUND",
    "message": "Document does not exist",
    "request_id": "19fb613e329-298ecef1",
    "details": {"document_id": "..."}
  }
}
```

- **`code` 是契约**，`message` 只是给人类看的。
- 前端业务逻辑只根据 `code` 决定交互；**不要** parse `message`。
- 详见 §4 错误码表。

## 4. 稳定错误码清单

v0.2 真实存在、已实现并可被触发的错误码：

| HTTP | code | 触发场景 |
|---:|---|---|
| 400 | `BAD_REQUEST` | 请求体 / 查询参数格式错误（兜底） |
| 400 | `UNSUPPORTED_FILE_TYPE` | （保留）M2 启用文件级校验时使用 |
| 400 | `FILE_TOO_LARGE` | （保留）M2 用 |
| 404 | `NOT_FOUND` | 通用兜底（HTTPException 触发） |
| 404 | `COLLECTION_NOT_FOUND` | 集合不存在 |
| 404 | `DOCUMENT_NOT_FOUND` | 文档不存在 |
| 404 | `TASK_NOT_FOUND` | 摄取任务不存在 |
| 404 | `QUERY_NOT_FOUND` | 查询记录不存在 |
| 404 | `INGESTION_NOT_FOUND` | 摄取记录不存在 |
| 404 | `IMAGE_NOT_FOUND` | 图片不存在 |
| 405 | `METHOD_NOT_ALLOWED` | 路由不接受的 HTTP 方法 |
| 409 | `CONFLICT` | 通用冲突兜底 |
| 409 | `COLLECTION_ALREADY_EXISTS` | 集合名重复 |
| 413 | `PAYLOAD_TOO_LARGE` | 上传文件 > 20 MB |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 上传文件 Content-Type 不在 allow-list |
| 422 | `VALIDATION_ERROR` | Pydantic 校验失败（body / query / path） |
| 500 | `INTERNAL_ERROR` | 未捕获异常（不会暴露内部细节） |
| 502 | `UPSTREAM_ERROR` | 第三方 Provider 失败 |
| 503 | `NOT_READY` | 依赖未就绪 / M1 stub 端点 |

**不要在业务代码里假设的 code**（v0.2 显式不发布）：

- `UNAUTHORIZED` / `FORBIDDEN` —— 当前无登录
- `RATE_LIMITED` —— 当前无应用层限流
- `retrying` 任务状态 —— 当前未实现自动重试

## 5. 端点速查

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/system/info` | 服务元信息 + Provider 状态（不返回 key） |
| GET | `/system/health` | 依赖健康快照 |
| GET | `/collections` | 集合列表（分页） |
| POST | `/collections` | 新建集合 |
| GET | `/collections/{id}` | 集合详情 |
| DELETE | `/collections/{id}` | 删除集合（含所有文档） |
| GET | `/collections/{id}/documents` | 文档列表（分页） |
| POST | `/collections/{id}/documents` | 上传文档 → 立即返回 `task_id`，轮询 `/tasks/{id}` |
| GET | `/documents/{id}` | 文档详情 |
| DELETE | `/documents/{id}` | 跨 4 存储协调删除 |
| GET | `/tasks/{id}` | 摄取任务状态 + 进度（仅摄取任务） |
| POST | `/collections/{id}/queries` | 检索查询（hybrid / dense / sparse，同步） |
| POST | `/collections/{id}/queries/async` | 异步检索（M3 批次2）→ 202 + `query_id`，轮询 result |
| GET | `/queries/{id}/result` | 异步查询结果（M3 批次2，pending/running/succeeded/failed） |
| GET | `/queries/{id}/trace` | 查询 Trace 时间线 |
| GET | `/ingestions/{id}/trace` | 摄取 Trace 时间线（M3 落地） |
| GET | `/images/{image_id}` | 受控图片字节 |

完整 OpenAPI Schema：[`openapi.v0.2.json`](openapi/openapi.v0.2.json)

代表性 examples：[`examples/`](openapi/examples/)（system-info、collections-list / list-empty、create、document-list / detail、document-upload-success / unsupported-media-type / payload-too-large、task-running / succeeded / failed、query-success / empty / degraded、trace、error-not-found / conflict / validation / internal）

## 6. 接口变更规则

1. 后端先改 OpenAPI（`docs/openapi/openapi.v0.2.json`）和实际实现。
2. 前端评审字段是否满足页面需求。
3. 双方确认后，后端实现；CI 跑快照测试确保 `app.openapi()` 与磁盘 JSON 完全一致。
4. 前端重新生成类型并接入。
5. **未经双方确认，不删除字段、不改字段类型、不改枚举含义**。
6. 破坏性变更必须升级主版本或提供迁移窗口。

变更检测（CI）：`tests/contract/test_openapi_snapshot.py` 会在 build 时对比；任何 OpenAPI 漂移都会让 PR 失败，提示运行 `python -m scripts.export_openapi` 刷新。

## 7. Mock 工作流

v0.2 阶段 System / Collection / Document / Upload / Task / Query / Image / Trace 端点已接通真实后端（见 handoff `docs/handoff-2026-07-31-m2-batch3.md`）。尚未接通的 UI 仍可本地 Mock（MSW 等）——**Mock 数据直接从 [`examples/`](openapi/examples/) 拷贝**，字段名、状态、ID 格式、时间字符串都要与 examples 完全一致，避免后端真实数据接入时再返工。

如果使用 TypeScript 类型生成，建议从 `openapi.json` 出发：
- `npx openapi-typescript http://127.0.0.1:8766/openapi.json -o src/api/types.ts`
- 或直接用仓库内 `npm run gen:types`（读 `docs/openapi/openapi.v0.2.json`）

## 8. 真实实现时间表

| 里程碑 | 状态 | 何时接通真实数据 |
|---|---|---|
| M1（边界和契约） | ✅ v0.2 已冻结 | 端点 stub，统一错误信封 |
| M2（核心数据闭环） | ✅ 已接通 | Collection / Document / Task / Query / Image 真实端点 |
| M3（诊断与视觉） | ✅ 已接通（除 rerank） | Trace API、多集合路由（批次1）、异步查询 + Task/Trace 落 SQLite + `last_query_id`（批次2）均已真实；`enable_rerank` 仍是 no-op |
| M4（文档与发布） | ✅ 已接通 | README + Makefile 统一启动 + 可选 Docker 部署 + 性能基线脚本 + 真实 `/system/health` |

**M3 批次 2 后新增/变化**：
- 新增 `POST /collections/{id}/queries/async`（202 `AsyncQueryAccepted`）+ `GET /queries/{query_id}/result`（轮询 `AsyncQueryResult`）；`query_id == task_id == trace_id`。
- `DocumentDetail` 新增可选字段 `last_query_id`。
- **Task / Trace / 异步查询结果已落 SQLite**（`data/db/web_api.db`）——进程重启后 `task_id` 不再 404（v0.2 已知限制已解除）。
- `/system/health` 为**真实依赖探测**：`embedding`（实际 embed 一次）/ `chroma`（客户端 heartbeat）/ `sqlite`（SELECT 1）/ `bm25`（数据目录可写）；探测带 5s TTL 缓存。`/system/info` 的 Provider `ready` 反映探测结果（embedding）或配置存在（llm/vision，不在检索热路径）。

> 契约一旦冻结（v0.2）就不得删字段/改类型/改枚举语义；变更需双方认可（TEAM_RESPONSIBILITIES §4.1）。

## 9. 反馈与变更

评审发现的问题请直接在本仓库开 issue，标 `area: web-api`；契约变更必须按 §6 流程走。
