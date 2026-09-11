# WeKnora 可用性优化借鉴：后端开发任务书

> 状态：待实施
> 文档日期：2026-09-11
> 目标分支：`feature/weknora-inspired-optimizations`
> 项目：`/home/hello/workspace/SKDY-RAG-SERVER`
> 参考项目：`/home/hello/workspace/WeKnora`
> 配套前端任务书：`docs/plan-2026-09-11-weknora-ux-frontend-tasks.md`

## 1. 目标与范围

本计划只实现四项能力所需的后端部分，且每个任务独立开发、测试、更新状态并提交：

1. 文档 Chunk 检查与原文定位；
2. 文档标签、文件夹、组合筛选和批量操作；
3. 可操作的摄取 Trace；
4. MCP Server 状态与受控连通测试。

借鉴 WeKnora 的知识管理、处理时间线和运维体验，但按 SKDY-RAG 当前 Python/FastAPI、`ApplicationServices`、只读 MCP 和 collection 授权体系重新实现，不迁移 WeKnora 的 Go 服务、多租户、Agent、Wiki 或 Neo4j。

## 2. 不可突破的边界

- 不改变 MCP 五只只读工具的名称、Schema 和授权语义。
- 不允许浏览器或公共 API 获得 `MCP_INTERNAL_API_KEY`。
- 不持久化、不记录、不回显连通测试中提交的完整 MCP Client Key。
- 文档文件夹是逻辑目录，不移动原始文件的物理路径。
- Chunk 在线编辑、重新向量化和版本历史不在本轮范围。
- 文件夹和标签不影响已有 collection 级授权；访问文档仍先验证 collection。
- Trace 响应不得包含密钥、Authorization Header、内部绝对路径或完整异常堆栈。
- 运行中任务取消必须是协作式取消，不杀进程、不破坏已提交数据。
- 所有列表接口必须有确定排序、分页上限和越权测试。
- 现有 API 保持兼容；新增字段优先使用可选字段。

## 3. 当前基线

| 能力 | 当前实现 | 本轮缺口 |
| --- | --- | --- |
| 文档详情 | `GET /api/v1/documents/{id}` 返回摘要及有限 Chunk 信息 | 无单 Chunk 全文、相邻导航、正文搜索、服务端分页 |
| 文档管理 | collection 下上传、列表、删除、重新上传 | 无标签、文件夹、组合筛选、批量操作 |
| Trace | 查询/摄取共用 `TraceResponse`，JSONL 持久化 | 阶段信息不完整，无 retry/cancel 能力声明和操作接口 |
| MCP 管理 | Key CRUD、白名单、独立 MCP 容器和 `/health` | 管理台无统一状态 API，无安全的协议级连通测试 |

关键实现位置：

- `src/web_api/routers/documents.py`
- `src/web_api/routers/collections.py`
- `src/web_api/routers/tasks.py`
- `src/web_api/routers/traces.py`
- `src/web_api/routers/mcp_keys.py`
- `src/application/services/document_service.py`
- `src/application/services/ingestion_service.py`
- `src/application/services/task_tracker.py`
- `src/application/services/trace_store.py`
- `src/application/services/web_store.py`
- `src/mcp_server/clients/`

## 4. API 契约冻结点

后端任务 B0 完成后，以下契约进入冻结期。后续若必须变更字段，先同步更新前端任务书、OpenAPI 快照和类型生成物。

统一规则：

- API 前缀为 `/api/v1`。
- 分页默认 `limit=50`，最大 `100`；Chunk 使用游标或 `(page,page_size)` 必须择一，本文统一采用 `page/page_size`。
- 错误继续使用项目现有 Error Envelope，并返回 Request ID。
- 不存在与无权访问文档时沿用项目公共 API 既有隐藏策略；不得泄露资源是否存在。
- 时间使用带时区 ISO 8601。
- 批量操作返回逐项结果，不因单项失败丢失成功结果。

## 5. 分阶段与原子任务

### B0：冻结契约与基线

#### B0.1 建立 OpenAPI 契约测试

产出：

- 为本文新增端点建立 Schema/示例快照。
- 对现有文档、Trace、MCP Key 响应建立兼容断言。
- 明确所有枚举、分页上限和错误码。

测试：新增 `tests/unit/test_ux_api_contracts.py`，先以待实现契约标记，再随各任务逐项转绿；不得整体 `xfail` 掩盖缺口。

提交主题：`test(api): freeze knowledge operations contracts`

门禁：现有 OpenAPI 测试、文档 API、Trace API、MCP Key API 测试通过。

---

### B1：Chunk 检查与原文定位

#### B1.1 单 Chunk 详情读取

新增：

`GET /api/v1/documents/{document_id}/chunks/{chunk_id}`

响应至少包含：

```json
{
  "chunk_id": "stable-id",
  "document_id": "stable-document-id",
  "index": 17,
  "text": "完整正文",
  "heading": "3.2 服务部署",
  "page": 12,
  "content_type": "text",
  "character_count": 864,
  "previous_chunk_id": "...",
  "next_chunk_id": "...",
  "source_locator": {"kind": "pdf_page", "page": 12}
}
```

实现要求：

- 先解析文档归属并验证 collection 访问，再读 Chunk。
- Chunk 必须确属该文档。
- 相邻关系使用稳定排序：`chunk_index` → 旧 ID 索引 → ID。
- 缺页码、标题或相邻项时返回 `null`，不报错。
- `source_locator.kind` 第一版支持 `pdf_page`、`image`、`section`、`none`。

测试：正常、首尾 Chunk、旧数据、错文档 Chunk、无权访问、缺失定位信息。

提交主题：`feat(api): expose authorized chunk details`

#### B1.2 文档 Chunk 分页、搜索和过滤

新增或扩展：

`GET /api/v1/documents/{document_id}/chunks?page=1&page_size=50&q=&content_type=&page_number=`

响应：

```json
{
  "items": [],
  "page": 1,
  "page_size": 50,
  "total": 0,
  "has_next": false
}
```

实现要求：

- `q` 去空白后最大 200 字符，执行大小写不敏感的字面量包含查询；第一版不引入全文检索引擎。
- `content_type` 使用白名单枚举。
- `page_number >= 1`；筛选页码不是分页页码。
- 稳定排序与 MCP `get_document_chunks` 一致，排序帮助函数应复用或下沉，不能复制两套规则。
- 列表项返回可预览摘要，单项全文由 B1.1 提供。
- 不再要求文档详情一次性携带所有 Chunk；兼容字段暂时保留，前端迁移完成后再单独评估废弃。

测试：首/中/尾/越界页、组合过滤、中文搜索、非法类型、上限、旧数据稳定顺序。

提交主题：`feat(api): paginate and filter document chunks`

#### B1.3 原文件定位契约

目标：规范不同文件格式的可定位能力，不改解析器。

实现要求：

- PDF 返回 1-based 页码。
- 图片返回文档预览 URL 所对应的整体图片定位。
- DOCX/Markdown/TXT 在已有 heading 时返回 `section`。
- 无可靠定位元数据时返回 `none`，禁止猜测。
- 预览 URL 仍走已有受控文档预览接口，不暴露磁盘路径。

测试：各类 locator 映射与降级。

提交主题：`feat(api): normalize chunk source locators`

B1 Phase Gate：B1 相关测试、MCP Chunk 回归、文档详情回归、OpenAPI 快照全部通过。

---

### B2：标签、文件夹、筛选与批量操作

#### B2.1 标签数据模型和迁移

新增逻辑表：

- `document_tags(id, collection_id, name, normalized_name, color, created_at, updated_at)`
- `document_tag_links(document_id, tag_id, created_at)`

约束：

- collection 内规范化名称唯一。
- 标签名去首尾空白，长度 1–64；颜色为受控 token，不接受任意 CSS。
- 删除标签级联删除关联，不删除文档。
- 存储实现同时覆盖项目实际使用的数据库测试路径。

测试：迁移、唯一性、级联、collection 隔离、Unicode 名称。

提交主题：`feat(store): add collection-scoped document tags`

#### B2.2 标签 CRUD 与文档绑定接口

端点：

- `GET/POST /api/v1/collections/{collection_id}/tags`
- `PATCH/DELETE /api/v1/collections/{collection_id}/tags/{tag_id}`
- `PUT /api/v1/documents/{document_id}/tags`

`PUT` 使用全量替换语义，body 为 `{"tag_ids": [...]}`，事务内完成。

测试：CRUD、绑定替换、跨 collection tag 拒绝、重复 ID、文档不存在、越权。

提交主题：`feat(api): manage document tags`

#### B2.3 文件夹数据模型和迁移

新增逻辑表：

- `document_folders(id, collection_id, parent_id, name, normalized_name, depth, created_at, updated_at)`
- 文档记录新增可空 `folder_id`。

约束：

- collection 内同级名称唯一。
- 最大深度 5。
- 禁止自身/子孙循环移动。
- 根目录由 `folder_id=null` 表示，不创建伪根记录。
- 删除非空文件夹返回 409；第一版不做递归强删。
- 不改变文档物理文件路径和稳定 ID。

测试：树、深度、循环、冲突、非空删除、collection 隔离、旧数据迁移。

提交主题：`feat(store): add logical document folders`

#### B2.4 文件夹 CRUD 与文档移动接口

端点：

- `GET/POST /api/v1/collections/{collection_id}/folders`
- `PATCH/DELETE /api/v1/collections/{collection_id}/folders/{folder_id}`
- `PUT /api/v1/documents/{document_id}/folder`

文件夹列表返回平面稳定列表（含 `parent_id`、`depth`、直接文档数），由前端组树；避免服务端和前端各定义不同嵌套规则。

测试：CRUD、移动到根、跨库拒绝、非空删除、并发冲突。

提交主题：`feat(api): manage logical document folders`

#### B2.5 文档组合筛选

扩展 collection 文档列表：

- `q`
- `folder_id`，另以明确值表示根目录
- `tag_id` 可重复，多标签语义固定为 AND
- `status`
- `file_type`
- `updated_after` / `updated_before`
- `sort=updated_desc|updated_asc|name_asc|name_desc|size_desc`

要求：

- 筛选在数据库/存储层完成，不能先取整库再由 Python 过滤。
- 分页排序必须带稳定 ID tie-breaker。
- 返回每篇文档的标签摘要和 `folder_id`。
- 保持旧调用不传参数时的结果语义。

测试：单条件、组合、标签 AND、空结果、稳定翻页、非法日期/排序、旧客户端兼容。

提交主题：`feat(api): filter and sort collection documents`

#### B2.6 批量标签操作

新增：`POST /api/v1/collections/{collection_id}/documents/batch/tags`

支持 `add`、`remove`、`replace`，最多 100 个文档 ID。响应逐项包含 `document_id/status/error`。

要求：预验证 collection 范围；单项不存在不得影响其他合法项；禁止把其他 collection 的标签或文档带入。

提交主题：`feat(api): batch update document tags`

#### B2.7 批量移动操作

新增：`POST /api/v1/collections/{collection_id}/documents/batch/move`

最多 100 项，支持移动到根。响应采用统一批量结果模型。

提交主题：`feat(api): batch move documents`

#### B2.8 批量重新解析

新增：`POST /api/v1/collections/{collection_id}/documents/batch/reprocess`

要求：

- 每项创建独立任务 ID。
- 已运行任务的文档返回明确冲突，不重复排队。
- 请求重试应通过 idempotency key 或任务状态避免重复执行。
- 最多 20 项，避免压垮解析服务。

提交主题：`feat(api): batch reprocess documents safely`

#### B2.9 批量删除

新增：`POST /api/v1/collections/{collection_id}/documents/batch/delete`

要求：

- 最多 100 项。
- 使用显式 POST action，避免 DELETE body 的代理兼容问题。
- 返回逐项结果；清理原文件、数据库、BM25、向量索引的语义复用单篇删除服务。
- 不在 Router 复制删除业务。

提交主题：`feat(api): batch delete collection documents`

B2 Phase Gate：迁移测试、标签/文件夹授权、组合分页、四类批量操作、既有上传/删除/列表回归全部通过。

---

### B3：可操作 Trace

#### B3.1 补全 Trace 阶段契约

为 `TraceStage` 增加可选字段：

- `status`: `pending|running|success|warning|failed|skipped|canceled`
- `input_count` / `output_count`
- `attempt`
- `skip_reason`
- `error_code`
- `error_summary`

为顶层 `TraceResponse` 增加：

- `status`
- `retryable`
- `cancelable`
- `attempt`
- `parent_trace_id`

要求：旧 JSONL 记录可以读取；缺字段按确定规则推导或保持 `null`，不能迁移时破坏历史 Trace。

测试：新旧序列化、敏感字段清洗、运行中/失败/跳过状态。

提交主题：`feat(trace): expose actionable stage state`

#### B3.2 运行中 Trace 查询一致性

要求：

- 运行中的任务能返回当前已完成阶段和当前阶段。
- JSONL 写入与内存态合并规则明确，终态优先且不能倒退。
- API 允许前端轮询，单次查询不全扫描无限日志。
- 找不到 Trace 但任务存在时继续返回兼容的空 Trace，并带任务状态。

提交主题：`fix(trace): keep live task timelines monotonic`

#### B3.3 摄取任务重试

新增：`POST /api/v1/tasks/{task_id}/retry`

要求：

- 仅 `failed|canceled` 可重试。
- 创建新任务与新 Trace，设置 `parent_trace_id` 和递增 attempt。
- 保留原任务与 Trace。
- 同一失败任务重复请求不得无限创建任务；使用幂等保护。
- 复用原始文档和解析配置；原文件缺失时返回可操作错误。

测试：允许状态、拒绝状态、幂等、原文件缺失、父子 Trace。

提交主题：`feat(tasks): retry failed ingestion attempts`

#### B3.4 摄取任务协作式取消

新增：`POST /api/v1/tasks/{task_id}/cancel`

要求：

- `pending|running` 可取消，其他终态返回稳定冲突。
- TaskTracker 保存取消请求；各长阶段在安全边界检查。
- 已完成的原子写入不回滚，后续阶段停止，并将任务置为 `canceled`。
- 不删除已有文档和旧索引；若新版本索引采用交换式提交，保持旧版本可用。
- 重复取消幂等。

测试：排队取消、阶段间取消、重复取消、完成后取消、终态 Trace。

提交主题：`feat(tasks): cancel ingestion cooperatively`

#### B3.5 Trace 列表查询

新增：`GET /api/v1/traces`

筛选：`type`、`status`、`collection_id`、`document_id`、`q`、时间范围；使用游标分页，默认按最新时间倒序。

要求：不能依赖每次请求完整扫描无界 JSONL。若当前 TraceStore 不支持索引，先增加轻量 SQLite 索引或有界索引文件；不得为了前端列表把全量日志读入内存。

提交主题：`feat(trace): list traces with bounded filters`

B3 Phase Gate：Trace 新旧兼容、状态单调、retry/cancel 并发、列表分页和现有 Trace 页面 API 回归通过。

---

### B4：MCP 状态与连通测试

#### B4.1 管理端 MCP 状态聚合接口

新增：`GET /api/v1/mcp-server/status`

由主 API 服务端探测 MCP 的匿名 `/health`，响应：

```json
{
  "status": "online",
  "mcp_url": "http://server:8765/mcp",
  "transport": "streamable-http",
  "version": "...",
  "upstream_status": "online",
  "checked_at": "...",
  "latency_ms": 12
}
```

要求：

- 对前端返回公开配置的客户端 URL，不返回容器内部 URL。
- 超时有硬上限，禁止跟随任意重定向，`trust_env=False`。
- URL 完全来自服务端配置，不接受请求传入，避免 SSRF。
- 状态枚举：`online|degraded|offline|misconfigured`。
- 匿名健康检查不得返回 collection 或密钥信息。

测试：在线、离线、超时、错误响应、上游异常、SSRF 不可控。

提交主题：`feat(api): report bounded mcp server status`

#### B4.2 连通测试错误分类

建立内部枚举：

- `server_unreachable`
- `handshake_failed`
- `client_unauthorized`
- `client_key_revoked`
- `internal_auth_failed`
- `upstream_unavailable`
- `empty_scope`
- `timeout`
- `unexpected_response`

每类返回用户安全的 `message` 和 `suggested_action`，内部异常仅进入受控日志，且日志过滤 Key/Header。

提交主题：`feat(mcp): classify connection diagnostics`

#### B4.3 使用一次性 Client Key 执行协议级测试

新增：`POST /api/v1/mcp-server/test-connection`

请求：

```json
{"api_key": "skdy_mcp_..."}
```

服务端依次执行：

1. 连接 MCP Streamable HTTP；
2. `initialize`；
3. `tools/list`；
4. `list_collections`；
5. 汇总阶段耗时和安全诊断。

响应不得包含原始 Key，至少包含：

```json
{
  "ok": true,
  "stages": [
    {"name": "connect", "status": "success", "latency_ms": 8},
    {"name": "initialize", "status": "success", "latency_ms": 15},
    {"name": "tools_list", "status": "success", "tool_count": 5},
    {"name": "list_collections", "status": "success", "collection_count": 2}
  ],
  "error": null,
  "tested_at": "..."
}
```

安全要求：

- body 中 Key 使用 `SecretStr` 或等效类型，模型 repr 不显示值。
- Key 只驻留请求生命周期内，不落数据库、Trace、审计 details 或异常文本。
- 禁止把 Key 拼进 URL。
- 请求大小限制；并发和频率限制。
- 只连接服务端配置的 MCP URL。
- 测试端点本身沿用可信管理端认证边界。

测试：正确/错误/撤销 Key、空 scope、工具缺失、MCP 在线但内部 API 鉴权失败、上游超时、日志无 Key。

提交主题：`feat(api): run ephemeral mcp connection tests`

#### B4.4 创建或轮换 Key 后的测试令牌边界

决策：不新增可恢复 Secret。前端只能在创建/轮换拿到完整 Key 的当前页面生命周期内调用 B4.3。后端数据库继续只保存摘要。

任务内容：

- 为 Key 创建、轮换和连通测试增加回归测试，证明 Secret 不会二次读取。
- OpenAPI 明确 `api_key` 为 write-only。
- Request/response 日志中对该路径禁用 body 捕获或执行字段级脱敏。

提交主题：`test(mcp): preserve one-time secret semantics`

B4 Phase Gate：真实 uvicorn + MCP Streamable HTTP 全链路测试通过；Key 不出现在日志、Trace、响应快照或持久化文件；原有 186+ 只读 MCP 测试继续通过。

## 6. 推荐执行顺序

严格依赖顺序：

```text
B0.1
 ├─ B1.1 → B1.2 → B1.3
 ├─ B2.1 → B2.2 → B2.5 → B2.6
 │          B2.3 → B2.4 → B2.5 → B2.7
 │                              ├─ B2.8
 │                              └─ B2.9
 ├─ B3.1 → B3.2 → B3.3
 │                 ├─ B3.4
 │                 └─ B3.5
 └─ B4.1 → B4.2 → B4.3 → B4.4
```

建议发布批次：

1. Batch A：B0 + B1；
2. Batch B：B3.1/B3.2 + B4；
3. Batch C：B2 标签和筛选；
4. Batch D：B2 文件夹和批量操作；
5. Batch E：B3 retry/cancel/Trace 列表。

不同纵向能力可并行，但同一存储层迁移不得并行修改同一 Schema 文件。

## 7. 每任务统一完成定义

每个任务必须：

1. 只实现该编号范围；
2. 更新 OpenAPI Schema 和示例；
3. 增加正常、边界、授权和错误测试；
4. 运行直接相关测试；
5. 运行所在 Phase Gate；
6. 执行 `git diff --check`；
7. 在独立 implementation status 文档记录命令和结果；
8. 一个清晰主题提交，不夹带前端或无关重构。

## 8. 总体验收门禁

- 现有 MCP 工具契约和隔离测试零回归。
- 文档列表/详情/上传/删除现有 API 零破坏。
- 所有新增资源执行 collection 隔离。
- 所有分页有稳定顺序、最大页大小和空页行为测试。
- 批量操作有部分成功语义和上限。
- Trace 状态不倒退，历史记录可读。
- retry/cancel 在并发请求下幂等。
- MCP 测试 Key 不落盘、不进日志、不回显。
- OpenAPI 生成物与前端 TypeScript 类型一致。
- 全量测试中的新增失败不得归因于“既有基线”跳过处理。

## 9. 明确不在本计划内

- Wiki 页面生成与维护；
- Neo4j/GraphRAG；
- Agent Runtime；
- Chunk 编辑与版本历史；
- 文档级复杂 RBAC；
- 文件夹拖拽上传后保留操作系统目录；
- 可恢复或再次查看 MCP Secret；
- 对任意用户提供任意 URL 的 MCP 探测器。
