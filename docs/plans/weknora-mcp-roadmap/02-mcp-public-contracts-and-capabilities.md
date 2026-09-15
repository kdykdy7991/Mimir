# 任务 02：MCP 公共契约与能力发现

> 状态：进行中（2026-09-15 启动）
> 前置：任务 01 Gate 通过
> 后继：任务 03

## 0. 实施状态流水（持续追加）

### 2026-09-15 启动与工作区保护

- 分支：`feature/weknora-inspired-optimizations`；HEAD：`a647f05`（docs: add task 02 developer prompt）。
- 启动时 `git status --short`：仅有未跟踪文件（用户既有文件，本任务全程不触碰、不暂存、不混入提交）：
  `.DS_Store`、`.codebuddy/`、`docs/.DS_Store`、`docs/prompts/.DS_Store`、
  `web/src/features/knowledge/` 下 11 个未跟踪 ts/tsx 文件、`web/src/test/mocks/viewport.ts`、
  `web/tests/e2e/document-detail.spec.ts`。
- 保护措施：仅 `git add <精确文件>` 提交本任务产物；不使用 `git add -A`、
  `git reset --hard`、`git checkout --`、`git clean`；不推送远端；每个 02.x 独立测试、独立提交；
  每批提交前 `git diff --check`。
- 环境说明（偏差记录）：任务书命令基于 `/home/hello/workspace/SKDY-RAG-SERVER` 与 `.venv`；
  本机仓库为 `/Users/dykong/Documents/Mimir`，启动时无 `.venv`。按 `uv.lock`（mcp 2.2.0）
  用 uv sync 建立 `.venv`（未跟踪，不入库），作为测试解释器；系统 anaconda 的 mcp 为 1.23.1，
  与本仓库 v2 构造 API 不兼容，不作为测试环境。

### 代码现状审计（2026-09-15，基于 HEAD `a647f05`）

1. **transport-neutral dataclass 现状**：`src/mcp_server/clients/models.py` 定义
   CollectionInfo / QueryRequest / EvidenceItem / Diagnostics / KnowledgeQueryResult /
   DocumentInfo / DocumentChunk / DocumentChunkPage（frozen dataclass，纯类型）。
   使用点：`clients/base.py`（Protocol）、`clients/in_process.py`、`clients/http_client.py`、
   `clients/__init__.py`、`web_api/internal_mcp.py`，以及约 12 个测试文件。
   问题（对本任务而言）：它位于 **mcp_server** 层，application 层无法反向复用；
   EvidenceItem 只有单一 `score: float` 与 `source_type`，无多阶段 scores/null 语义。
   本任务**不移动、不破坏**该模块，新 application 契约独立建立，02.2 mapper 负责二者适配。
2. **5 工具 `_format` 重复与差异**：query 工具自带 `_format`（markdown + structured，
   含 `_excerpt` 200 字符硬编码、score 按 `:.4f` 渲染、兼容 n_results/citations）；
   list_collections、get_document、get_document_chunks 各自 `_render` + markdown 函数；
   结构化字段手工拼装、重复严重。所有工具均返回 `(markdown, structured_dict)` 元组，
   经 `ProtocolHandler._normalize_result` 走 structured content。
3. **ProtocolHandler**：`ToolRegistration(name/description/input_schema/handler/output_schema)`；
   `build_server()` 用 mcp v2 构造 API（`on_list_tools`/`on_call_tool`）；stdio 与
   streamable-http 共用同一 Server 注册结果。`tool_error()` → CallToolResult(is_error=True)；
   handler 未知异常协议级传播。**当前未注册任何 resource handler**。
4. **MCP SDK Resource 能力**：uv.lock 钉 `mcp==2.2.0`（Server 为 FastMCP 内核），
   支持 `@server.list_resources()` / `@server.read_resource()` 装饰器与
   `Resource` / `ReadResourceResult` / `TextResourceContents` 类型，
   支持静态/模板 Resource 和 Resource Link；`create_connected_server_and_client_session`
   可做内存双通道验证。结论：**优先 Resource 方案可行**，无需退回第 6 只工具。
5. **现有上限及环境覆盖**：query 长度 2000 与 top_k 1..50 默认 10 硬编码在
   `tools/query_knowledge_hub.py`（MAX_QUERY_LENGTH 与 schema 字面量）；page_size 1..50
   默认 20、page≥1 同时硬编码在 `tools/get_document_chunks.py` 的 schema、handler 与
   `clients/in_process.py:488-491`（InvalidRequestError）三处；正文无字符预算
   （`_excerpt` 仅 markdown 截 200，structured `text` 全量返回）。
   `Settings`/`McpServerSettings` 无任何预算字段；环境覆盖仅见
   `DOCUMENT_PARSER_BACKEND`、`MCP_SERVER_PUBLIC_BASE_URL` 两处 env 注入先例。
6. **重复常量盘点**：`50`（top_k/page_size 上限）、`20`/`10`（默认页大小/top-k）、
   `2000`（query 上限）、`200`（markdown excerpt）分散在工具 schema、handler、
   in_process client 三处，无单一事实源。
7. **错误现状**：clients/errors.py 有 ReadonlyClientError 基类 + InvalidRequest /
   ResourceNotFound / AccessDenied / UpstreamUnavailable / UpstreamTimeout；
   工具级错误返回固定英文文案；query 基础设施错误经 in_process 包成
   `knowledge retrieval failed: <ExcType>` 协议级错误。**无 rate_limited / overloaded**。
   HTTP client 已有 408/504→timeout、5xx→unavailable 的映射，无 429 区分。
8. **OpenAPI**：REST/internal DTO 不引用 mcp_server clients 模型以外的契约
   （internal_mcp 仅用 QueryRequest），本任务新增 `application/contracts` 不会被
   FastAPI schema 引用 → **不刷新 OpenAPI**；02.5 以 `export_openapi --check` 实证无 diff。
9. **capability 事实源**：工具清单可直接从 `ProtocolHandler` 注册表（list_names/get）
   生成；限制从 Settings 新增预算节生成；retrieval modes 应用层固定为
   hybrid/dense/sparse（QueryService.search）；rerank 由 settings.rerank.backend != none
   决定；filters/父子块/多库/多查询/写工具按实现注册情况固定 false 事实，不另写手编名单。

任务书与代码的偏差：无实质性冲突；任务书仓库路径与 `.venv` 路径不同（见上，已用 uv 解决）。

### 02.1 应用层传输无关契约

- 状态：done
- 新增文件：
  - `src/application/contracts/__init__.py`（公共出口；`CONTRACT_VERSION="evidence-v1"`）
  - `src/application/contracts/serialization.py`（**唯一** JSON-safe 序列化入口
    `to_jsonable`/`to_json`；`ContractError`；构造工具：non-empty 字符串、
    元组去重保序、aware datetime、unknown key 忽略策略）
  - `src/application/contracts/evidence.py`（`EvidenceScores`、`SourceLocator`、
    `EvidenceV1`）
  - `src/application/contracts/filters.py`（`TagOperator`、`EvidenceFilterV1`）
  - `src/application/contracts/messaging.py`（`WarningCode`、`WarningV1`、
    `EvidencePageV1`）
  - `tests/unit/application/test_evidence_contracts.py`（34 项）
- 关键决策：
  - 全部 frozen dataclass + tuple/MappingProxyType，构造期 `__post_init__` 强校验；
  - `scores` 四阶段固定形状，未执行 = `null`（非 0、不省略），禁 NaN/Inf；
  - `SourceLocator` 只有 kind/page/heading 相对定位，拒绝 POSIX/Windows 绝对路径；
  - Evidence 九项可选未来字段（版本/parent/title/content/preview/heading_path/
    asset_ids/indexed_at）默认 None，**不从旧数据猜测**；
  - Filter：缺失=不限制、显式空列表非法、去重保序、descendants 必须有 folder、
    时间必须带时区、after≤before；本任务不接检索；
  - Warning 四码冻结为字符串枚举；page 与 cursor 分页互斥；truncated_* 为 true 时
    强制带 `truncated` warning（禁止无提示截断）；
  - unknown optional field 读取策略：`from_mapping` 经 `known_kwargs` 显式忽略，
    永不回吐（前向兼容）；
  - 契约包仅依赖标准库；子进程断言导入后 sys.modules 无 mcp/fastapi/starlette/
    uvicorn/httpx/chromadb/pydantic/openai/src.mcp_server/src.web_api/src.libs/
    src.core。
- 旧 `src/mcp_server/clients/models.py` 未改动（5 工具 Client 契约保持），
  新契约独立存在，适配留到 02.2。
- 测试命令与结果：
  ```bash
  python3 -m pytest tests/unit/application/test_evidence_contracts.py -q
  # 34 passed in 0.10s
  python3 -m pytest tests/unit/test_architecture_boundary.py -q
  # 15 passed in 0.07s
  git diff --check   # 无输出
  ```
- 遗留问题：无。

### 02.2 集中 MCP DTO 与兼容映射

- 状态：done
- 新增/修改文件：
  - `src/mcp_server/presentation/__init__.py`、`evidence_mapper.py`（新增；
    纯函数，不访问 DB/Client/Embedding/检索；运行时只依赖 application 契约，
    clients.models 仅 TYPE_CHECKING 引用）
  - `src/mcp_server/tools/query_knowledge_hub.py`（删除本地 `_format`/
    `_excerpt`/`_EMPTY_HINT`，委托 mapper；schema/handler 一字未动）
  - `tests/unit/test_evidence_mapper.py`（29 项）
- 设计与兼容证据：
  - `format_query_result()` 是 query 工具 markdown+structured 的**唯一**实现：
    空态 hint、References 段、score `:.4f`、page p.N/n/a、n_results/citations
    别名逐字保持；inventory `--check` 对 5 工具快照 **零 diff**（未重新生成）。
  - 新 v1 路径：`evidence_v1_from_legacy()` → EvidenceV1；旧单一 score **只**
    按 `source_type` 落到 dense/sparse/fusion/rerank 之一，其余 null；
    未知 source_type → 四值全 null（不猜测）；版本/parent/assets 永不编造。
  - 定位：有 page → `{kind:"page",page}`；无 page → `{kind:"chunk",page:null}`。
  - 安全清洗 `redact_sensitive()`/`safe_preview()`：Bearer/Authorization 头、
    `skdy_mcp_*` key、api_key/token/secret/password 赋值、POSIX（≥2 段，
    URL 与相对路径不误伤）与 Windows 绝对路径、traceback 块统一脱敏；
    v1 `content_preview` 先脱敏再截断（保留旧 `…` 截断语义）。
  - **暂留点（Task 04 删除双实现）**：旧 structured `text`/`source` 等字段维持
    原样全量输出（含原文中的路径字面量，属文档内容而非环境泄漏）；v1 行结构
    （scores/source_locator/matched_queries）尚未追加到 5 工具输出 schema，
    避免在映射提交中混入契约事件；02.5 决定是否追加并更新快照。
  - markdown 摘要现在经过脱敏（仅在命中间谍特征时与旧输出不同，属安全加固）。
- 测试命令与结果（系统 Python，mcp 1.23；build_server 4 项失败是项目要求
  mcp 2.2 的环境差异，非本提交引入，.venv 就绪后复跑）：
  ```bash
  python3 -m pytest tests/unit/test_evidence_mapper.py \
      tests/unit/application/test_evidence_contracts.py -q
  # 63 passed
  python3 -m pytest tests/unit/test_protocol_handler.py tests/unit/test_query_knowledge_hub.py \
      tests/unit/test_list_collections.py tests/unit/test_get_document.py \
      tests/unit/test_get_document_summary.py tests/unit/test_get_document_chunks.py \
      tests/unit/test_readonly_compat.py tests/unit/test_mcp_contract_v1_snapshot.py \
      tests/unit/test_mcp_readonly_invariants.py -q
  # 80 passed, 4 failed（4 项均为 build_server v2 构造 API，系统 mcp 1.23 不支持）
  python3 scripts/mcp_contract_inventory.py --check
  # ok: fixture is current (5 tools), exit 0
  ```
- 遗留问题：无（4 项环境性失败待 .venv/mcp 2.2 复跑消除）。

## 1. 目标

在增加工具前冻结统一的 Evidence、Filter、分页、预算、Warning 和 Error 模型，并提供机器可读能力发现。
本阶段先建立类型和映射，避免后续工具各自发明响应结构。

## 2. 非目标

- 不实现新检索算法；
- 不增加父子分块和多 collection 行为；
- 不改变旧工具的必填字段或错误语义；
- 不依赖 Chroma、BM25 或特定 MCP transport 的私有类型。

## 3. 契约决策

### Evidence v1

必填：`collection_id`、`document_id`、`chunk_id`、`content_type`、`source_locator`、`scores`、
`matched_queries`。可选：版本、parent ID、标题、正文/预览、heading path、asset IDs、indexed_at。

`scores` 固定包含 dense/sparse/fusion/rerank，未执行为 null；禁止把不同量纲压成含义不明的单一 score。

### Filter v1

支持 collection/document/tag/folder/file/content/source/time；`tag_operator=and|or`；
`include_descendants` 只在 folder 存在时生效。空数组按非法参数处理，字段缺失代表不限制。

### Warning/Error v1

Warning 至少覆盖 truncated、rerank_degraded、legacy_metadata_missing、partial_collection_failure。
Error 至少区分 invalid_request、not_found_or_not_accessible、upstream_timeout、upstream_unavailable、
rate_limited、overloaded。工具级失败不得伪装为空 evidence。

## 4. 原子任务

### 02.1 应用层契约类型

在合适的 application/query contracts 模块定义不可变类型及序列化测试；禁止从 MCP SDK 类型反向依赖。

提交：`refactor(query): add transport-neutral evidence contracts`

### 02.2 MCP DTO 与旧响应映射

实现单一 mapper，把应用层结果映射为 MCP structured content；旧工具继续输出兼容字段，新字段只做可选追加。

提交：`refactor(mcp): centralize evidence response mapping`

### 02.3 预算和分页校验器

统一 top-k、page size、返回字符数、query 数/长度上限。配置启动时校验；请求越界返回稳定错误。

提交：`feat(mcp): enforce bounded evidence responses`

### 02.4 能力发现

优先注册只读 MCP Resource，例如 `rag://server/capabilities`；若当前 SDK/Client 对静态 Resource 支持不足，
使用 `get_server_capabilities`。返回 contract version、工具版本、模式、过滤器、上限、内容类型及尚未支持能力。

提交：`feat(mcp): expose versioned server capabilities`

### 02.5 契约快照与 OpenAPI 对齐

更新 MCP Schema 快照；若 REST 共享 DTO，同步 OpenAPI 与 Web 生成类型。增加测试确保 `scores` null 语义、
路径清洗、时间格式和 unknown optional field 的向前兼容。

提交：`test(contract): freeze evidence and capabilities v1`

## 5. 测试与验收

- 应用类型不导入 MCP/Web 包；
- 旧五工具快照保持兼容；
- 每类 Warning/Error 有序列化测试；
- 超大 top-k、过长 query、超预算正文均被拒绝或显式截断；
- capability 与实际注册工具、配置上限一致；
- Evidence 中不出现绝对路径、Secret 或任意异常堆栈。

## 6. Gate 与回滚

Gate：公共契约和 capability 快照通过；旧客户端集成测试通过；所有限制有单一配置来源。

回滚：新字段均为可选；关闭 capability 注册即可回滚，不删除旧 mapper，直至任务 04 完成统一切换。

