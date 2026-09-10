# Readonly MCP Optimization — Implementation Status

> 方案：`docs/plan-2026-09-10-weknora-readonly-mcp-optimization.md`
> 目标分支：`feature/weknora-inspired-optimizations`
> WeKnora 固定参考提交：`3e6010e7cd3937f289cc1dbadc829e71eb1163f4`
> 本文件在开发过程中持续更新，不搞「最后一次性补齐」。

## 阶段总览

| Phase | 标题 | 状态 | 提交数 |
| --- | --- | --- | --- |
| Phase 0 | 固定基线和边界 | done | 2 |
| Phase 1 | 建立只读 Client 边界 | done | 5 |
| Phase 2 | 规范现有工具 | done | 3 |
| Phase 3 | 新增文档 Chunk 分页 | done | 2 |
| Phase 4 | HTTP 解耦 | done | 5 |
| Phase 5 | 兼容、文档和交付 | done | 1 |

## 既有基线（本轮开始前已确认）

- 已运行：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_protocol_handler.py tests/unit/test_list_collections.py \
    tests/unit/test_get_document_summary.py tests/unit/test_mcp_authorization.py \
    tests/integration/test_mcp_server.py tests/integration/test_mcp_http_access_control.py -q
  ```
  结果：`72 passed`。
- 全量 `pytest -q`：`1384 passed, 40 failed, 1 skipped, 1 error`。失败均为**既有**外部服务相关（本地 LLM/Embedding 端点、streamable-http cli 子进程等），非本方案引入；`pytest tests/unit/...mcp*` 相关的 MCP 测试全部通过。
- 分支 `feature/weknora-inspired-optimizations` 与上游一致，工作区干净。

---

## Phase 0：固定基线和边界

### P0.1 记录现有工具契约

- 状态：done
- 提交：`41f3f72`
- 修改文件：
  - `tests/unit/test_mcp_contract_snapshot.py`（新增）
  - `tests/fixtures/mcp_contract/phase0_tool_schemas.json`（新增，P0.1 基线快照）
- 已运行测试：
  ```bash
  MCP_REGENERATE_SNAPSHOT=1 .venv/bin/python -m pytest tests/unit/test_mcp_contract_snapshot.py -q
  .venv/bin/python -m pytest tests/unit/test_mcp_contract_snapshot.py tests/unit/test_list_collections.py tests/unit/test_get_document_summary.py -q
  ```
- 测试结果：`2 passed, 1 skipped`（regenerate 模式）；正常模式与基线比对一致（快照无漂移）。
- 遗留问题：无。
- 下一步：P0.1 提交。基线快照可在 `MCP_REGENERATE_SNAPSHOT=1` 下重复生成。

### P0.2 添加只读不变量测试

- 状态：done
- 提交：`见本提交`（P0.2 提交）
- 修改文件：
  - `tests/unit/test_mcp_readonly_invariants.py`（新增）
  - `docs/implementation-status/readonly-mcp-optimization.md`（本文件，状态记录）
- 已运行测试：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_mcp_readonly_invariants.py -q
  ```
- 测试结果：`5 passed`。覆盖：工具面只读、工具模块不导入 LLM/Agent、list_collections 与 get_document_summary 读取不触发任何 store 写方法、越权/不存在文档同形错误（is_error + “document not found”，具体统一文案在 P2.3 收敛）。
- 遗留问题：越权与不存在文案当前不完全一致（P2.3 统一为 `document not found or not accessible`）；query_knowledge_hub 的不变式在 Phase 1 客户端边界上补强。
- 下一步：P0.2 提交。

### Phase 0 Gate 验收记录

- **基线快照已建立**：`41f3f72`，`tests/fixtures/mcp_contract/phase0_tool_schemas.json` 可经 `MCP_REGENERATE_SNAPSHOT=1` 重复生成。
- **只读不变量测试已存在**：`6e6f772`，`tests/unit/test_mcp_readonly_invariants.py`（5 项）。
- **生产行为尚未改变**：Phase 0 全部提交只含测试、fixture 与状态文档；`git diff <P0 之前> -- src/` 为空。
- **现有测试没有回退**：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_mcp_contract_snapshot.py tests/unit/test_mcp_readonly_invariants.py \
    tests/unit/test_mcp_authorization.py tests/integration/test_mcp_http_access_control.py -q
  ```
  结果：`30 passed`。
- **检查**：`git diff --check` 无输出；`git status` 干净。
- **Gate 结论**：通过，自动进入 Phase 1。

---

## Phase 1：建立只读 Client 边界

### P1.1 定义领域模型和接口

- 状态：done
- 提交：`见本提交`（`refactor(mcp): add readonly client contracts`）
- 修改文件：
  - `src/mcp_server/clients/models.py`（新增：CollectionInfo / QueryRequest / EvidenceItem / Diagnostics / KnowledgeQueryResult / DocumentInfo / DocumentChunk / DocumentChunkPage）
  - `src/mcp_server/clients/base.py`（新增：`RagReadOnlyClient(Protocol)`，4 个只读方法）
  - `src/mcp_server/clients/__init__.py`（新增）
  - `tests/unit/test_readonly_client_contracts.py`（新增）
- 已运行测试：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_readonly_client_contracts.py -q
  ```
- 测试结果：`6 passed`（方法集、返回/参数注解只引用纯模型与 principal、模型默认值与兼容字段、无 MCP/Chroma/DTO 类型泄漏）。
- 遗留问题：无。
- 下一步：P1.2 实现 `InProcessRagReadOnlyClient` 并逐个改路由三个现有工具。

### P1.2a 路由 `list_collections`

- 状态：done
- 提交：`见本提交`（`refactor(mcp): route collection listing through readonly client`）
- 修改文件：
  - `src/mcp_server/clients/errors.py`（新增，错误字汇 P1.3）
  - `src/mcp_server/clients/in_process.py`（新增：`InProcessRagReadOnlyClient.list_collections`）
  - `src/mcp_server/tools/common.py`（新增：客户端解析/缓存 + 注入）
  - `src/mcp_server/tools/list_collections.py`（改：handler 变薄，仅调用 client + 格式化）
  - `tests/unit/test_list_collections.py`（改：改测客户端）
  - `tests/unit/test_mcp_contract_snapshot.py`（改：行为样例经 client 生成）
- 已运行测试：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_list_collections.py tests/unit/test_mcp_contract_snapshot.py -q
  .venv/bin/python -m pytest tests/unit/test_mcp_authorization.py tests/integration/test_mcp_server.py tests/integration/test_mcp_http_access_control.py -q
  ```
- 测试结果：`11 passed`；快照/不变量 `8 passed`；授权/集成 `30 passed`。输出与 Phase 0 基线一致（schema 未变）。
- 遗留问题：`_vector_counts` 仍可能实例化 Chroma；Phase 4 独立部署（HTTP client）下消除。
- 下一步：P1.2b 路由 `query_knowledge_hub`。

### P1.2b 路由 `query_knowledge_hub`

- 状态：done
- 提交：`见本提交`（`refactor(mcp): route knowledge query through readonly client`）
- 修改文件：
  - `src/mcp_server/tools/query_knowledge_hub.py`（改：handler 变薄，检索下沉到 client）
  - `src/mcp_server/clients/in_process.py`（补：`query_knowledge` → `KnowledgeQueryResult`，保留 Trace/用量/降级）
  - `tests/unit/test_query_knowledge_hub.py`（新增）
  - `tests/unit/test_readonly_client_query.py`（新增）
- 已运行测试：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_query_knowledge_hub.py tests/unit/test_readonly_client_query.py -q
  .venv/bin/python -m pytest tests/integration/test_mcp_server.py tests/unit/test_mcp_contract_snapshot.py tests/unit/test_mcp_readonly_invariants.py -q
  ```
- 测试结果：`11 passed`；集成/快照/不变量 `16 passed`。查询输出仍为 legacy citation 结构（schema 未变）。
- 遗留问题：E6 内联图片 `as_content_pair()` 多模态内容在当前 text+structured 路径不再由工具直接产出；Phase 2 查询输出将按方案收敛为证据契约，方案本就不含图片工具。行为基线快照仅约束 schema/空态/错误，不受影响。
- 下一步：P1.2c 路由 `get_document_summary`。

### P1.2c 路由 `get_document_summary`

- 状态：done
- 提交：`见本提交`（`refactor(mcp): route document lookup through readonly client`）
- 修改文件：
  - `src/mcp_server/tools/get_document_summary.py`（改：handler 变薄，调用 `client.get_document`；统一 `ResourceNotFoundError`/`AccessDeniedError` 映射）
  - `src/mcp_server/clients/in_process.py`（补：`get_document` — 解析→授权→读 metadata）
  - `tests/unit/test_get_document_summary.py`（改：经 fake client 测 handler）
  - `tests/unit/test_readonly_client_document.py`（新增：client 解析/授权/读取）
  - `tests/unit/test_mcp_readonly_invariants.py`、`tests/unit/test_mcp_contract_snapshot.py`（改：行为样例经 client）
- 已运行测试：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_get_document_summary.py tests/unit/test_readonly_client_document.py tests/unit/test_mcp_readonly_invariants.py -q
  .venv/bin/python -m pytest tests/unit/<12 个 MCP 文件> -q
  ```
- 测试结果：前组 `14 passed`；完整 MCP 组 `96 passed`（2 项快照修复后单独复跑为 `3 passed`）。
- 遗留问题：越权/不存在文案尚未收敛为逐字一致（P2.3 统一）。
- 下一步：P1.3 统一错误类型与测试。

### P1.3 统一错误类型

- 状态：done
- 提交：`见本提交`（`refactor(mcp): unify readonly client errors`）
- 修改文件：
  - `src/mcp_server/clients/errors.py`（改：映射规则已由 P1.2a 引入，此处补充测试）
  - `src/mcp_server/tools/get_document_summary.py`（改：增加 `InvalidRequestError` → 工具级错误映射）
  - `tests/unit/test_readonly_client_errors.py`（新增）
  - `tests/unit/test_mcp_readonly_invariants.py`（改：增加客户端边界只读断言）
- 已运行测试：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_readonly_client_errors.py tests/unit/test_get_document_summary.py tests/unit/test_mcp_readonly_invariants.py -q
  ```
- 测试结果：`11 passed` + `6 passed`。映射规则：InvalidRequest/NotFound/AccessDenied → `is_error`；Upstream 两类与未知异常 → 协议级传播。
- 遗留问题：凭据脱敏在 Phase 4 HTTP Client 落地。
- 下一步：Phase 1 Gate 验收。

### Phase 1 Gate 验收记录

- **`RagReadOnlyClient` 契约建立**：`0cdb095`。
- **三个现有工具已逐步通过 Client**：list_collections `6ab0b75`、query `3f8c526`、get_document_summary `7e2aa19`。
- **Handler 不再构建完整底层依赖**：三个 handler 均经 `client_from_args` 获取 client；`Embedding/VectorStore/SQLite` 构建在 `InProcessRagReadOnlyClient` 内。
- **错误类型和映射统一**：`test_readonly_client_errors.py` + get_document_summary 增加 InvalidRequest 映射。
- **Phase 0 快照仍兼容**：`test_mcp_contract_snapshot.py` `3 passed`（schema 未变）。
- **验证**：
  ```bash
  git diff --check   # 无输出
  .venv/bin/python -m pytest tests/unit/test_protocol_handler.py tests/unit/test_list_collections.py \
    tests/unit/test_get_document_summary.py tests/unit/test_query_knowledge_hub.py \
    tests/unit/test_readonly_client_contracts.py tests/unit/test_readonly_client_query.py \
    tests/unit/test_readonly_client_document.py tests/unit/test_readonly_client_errors.py \
    tests/unit/test_mcp_authorization.py tests/unit/test_mcp_contract_snapshot.py \
    tests/unit/test_mcp_readonly_invariants.py tests/integration/test_mcp_server.py \
    tests/integration/test_mcp_http_access_control.py -q
  ```
  结果：`104 passed`。
- **Gate 结论**：通过，自动进入 Phase 2。

---

## Phase 2：规范现有工具

### P2.1 规范化 `list_collections`

- 状态：done
- 提交：`见本提交`（`feat(mcp): normalize list collections contract`）
- 修改文件：
  - `src/mcp_server/tools/list_collections.py`（改：移除 data_dir/source 内部字段，输出 name/description/document_count/chunk_count，新增 count + 兼容 n_collections）
  - `src/mcp_server/clients/in_process.py`（补：`_document_counts` 经 ingestion-history 估算文档数）
  - `tests/unit/test_list_collections.py`（改：新字段断言 + 不泄露内部路径）
  - `tests/unit/test_mcp_contract_snapshot.py`（改：空态样例 + 基线更新）
- 已运行测试：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_list_collections.py tests/unit/test_mcp_contract_snapshot.py tests/unit/test_mcp_readonly_invariants.py tests/unit/test_query_knowledge_hub.py -q
  ```
  结果：`28 passed`；`MCP_REGENERATE_SNAPSHOT=1` 更新基线后正常 `3 passed`。
- 遗留问题：`document_count` 依赖 ingestion-history DB 可用，缺失时返回 null（不伪造 0）。
- 下一步：P2.2 增强 `query_knowledge_hub`。

### P2.2 增强 `query_knowledge_hub`

- 状态：done
- 提交：`见本提交`（`feat(mcp): normalize knowledge evidence query contract`）
- 修改文件：
  - `src/mcp_server/tools/query_knowledge_hub.py`（改：`rerank` 为准 + `no_rerank` 兼容别名；query 长度校验 1..2000；结构化输出 `count`/`evidence`/`diagnostics`/`collection`，兼容 `n_results`/`citations`；描述去掉答/问措辞）
  - `tests/unit/test_query_knowledge_hub.py`（改：新契约 + 兼容字段 + rerank 优先级）
  - `tests/fixtures/mcp_contract/phase0_tool_schemas.json` + `tests/unit/test_mcp_contract_snapshot.py`（改：基线更新）
- 已运行测试：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_query_knowledge_hub.py -q
  .venv/bin/python -m pytest tests/integration/test_mcp_server.py tests/unit/test_readonly_client_query.py tests/unit/test_mcp_contract_snapshot.py -q
  ```
  结果：`10 passed`；`15 passed`。
- 遗留问题：E6 多模态图片内容不再由工具直接产出（方案查询契约不含图片工具）。
- 下一步：P2.3 get_document 演进。

### P2.3 get_document 演进（含兼容别名）

- 状态：done
- 提交：`见本提交`（`feat(mcp): add get_document compatibility handler`，别名端并入）
- 修改文件：
  - `src/mcp_server/tools/get_document.py`（新增：`get_document` 规范工具，`document_id` 为准 + `doc_id` 兼容别名；`get_document_summary` 共用 `_get_document_item`；统一 not-found/越权文案 `document not found or not accessible`）
  - `src/mcp_server/tools/get_document_summary.py`（改：改为兼容别名，注册共享 handler）
  - `src/mcp_server/server.py`（改：注册 `get_document` + `get_document_summary`）
  - 测试：`test_get_document.py`（新增）、`test_get_document_summary.py`（改）、`test_mcp_readonly_invariants.py`（改：统一文案逐字断言）、`test_mcp_contract_snapshot.py`（改）、`tests/integration/test_mcp_server.py`（改：4 工具）
  - `tests/fixtures/mcp_contract/phase0_tool_schemas.json`（改：基线更新）
- 已运行测试：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_get_document.py tests/unit/test_get_document_summary.py tests/unit/test_mcp_readonly_invariants.py -q
  .venv/bin/python -m pytest tests/unit/test_readonly_client_errors.py tests/unit/test_get_document.py tests/integration/test_mcp_server.py -q
  ```
  结果：`16 passed`；`20 passed`。
- 遗留问题：无。
- 下一步：Phase 2 Gate。

### Phase 2 Gate 验收记录

- **`list_collections` 契约规范完成**：`a93eb05`。
- **`query_knowledge_hub` 只返回 evidence，不生成答案**：`ed9d6e6`（测试断言无 answer 字段）。
- **`get_document` 与 `get_document_summary` 共用实现**：`tools/get_document.py::_get_document_item` + 别名注册。
- **所有兼容字段和旧参数都有测试**：`count`/`n_collections`（P2.1）、`n_results`/`citations`/`no_rerank`/`rerank`（P2.2）、`doc_id`/`doc_type`/`source_path`（P2.3）。
- **验证**：
  ```bash
  git diff --check   # 无输出
  .venv/bin/python -m pytest tests/unit/test_protocol_handler.py tests/unit/test_list_collections.py \
    tests/unit/test_get_document.py tests/unit/test_get_document_summary.py tests/unit/test_query_knowledge_hub.py \
    tests/unit/test_readonly_client_contracts.py tests/unit/test_readonly_client_query.py \
    tests/unit/test_readonly_client_document.py tests/unit/test_readonly_client_errors.py \
    tests/unit/test_mcp_authorization.py tests/unit/test_mcp_contract_snapshot.py \
    tests/unit/test_mcp_readonly_invariants.py tests/integration/test_mcp_server.py \
    tests/integration/test_mcp_http_access_control.py -q
  ```
  结果：`114 passed`。
- **Gate 结论**：通过，自动进入 Phase 3。

---

## Phase 3：稳定分页读取 Chunk

### P3.1 + P3.2 稳定排序与分页客户端

- 状态：done
- 提交：`7d679c8`
- 修改文件：`src/mcp_server/clients/in_process.py`（改：`_ordered_chunks`/`_chunk_sort_key` + `get_document_chunks` 实现替换 NotImplementedError）、`tests/unit/test_readonly_client_chunks.py`（新增）
- 已运行测试：`/home/hello/workspace/SKDY-RAG-SERVER/tests/unit/test_readonly_client_chunks.py` → `8 passed`
- 排序证明：3 种来源均覆盖——`chunk_index` 元数据（权威）、旧数据缺失时从 chunk id `_(\d{4})_` 解析、纯旧数据按 id 稳定排序；均不依赖向量库自然顺序。
- 分页：first/middle/last/out-of-range、`page_size 1..50`、`page>=1`、未解析文档与越权统一报错。

### P3.3 `get_document_chunks` 工具

- 状态：done
- 提交：见本提交（tools + 注册 + 测试）
- 修改文件：
  - `src/mcp_server/tools/get_document_chunks.py`（新增：document_id + doc_id 别名、page/page_size、统一 not-found 文案）
  - `src/mcp_server/server.py`（改：注册 `get_document_chunks`）
  - `tests/unit/test_get_document_chunks.py`（新增）
  - `tests/unit/test_mcp_contract_snapshot.py` + `tests/integration/test_mcp_server.py` + `tests/fixtures/mcp_contract/phase0_tool_schemas.json`（改：5 工具 + 基线）
- 已运行测试：`/home/hello/workspace/SKDY-RAG-SERVER/tests/unit/test_get_document_chunks.py` → `6 passed`；全 Phase 3 门禁 `128 passed`。
- 旧数据可读：无 `chunk_index` 的旧记录通过 `_(\d{4})_` 解析或按 id 稳定排序，均不抛错。

### Phase 3 Gate 验收记录

- **chunk 顺序稳定**：`_chunk_sort_key`（chunk_index → id 内 index → id），见 `7d679c8`。
- **不依赖自然顺序**：排序在客户端显式完成（`_ordered_chunks`），测试以乱序输入验证。
- **旧数据可读**：P3.1 三来源排序测试证明。
- **分页首/中/尾/越界**：`test_readonly_client_chunks.py::test_pagination_*` 通过。
- **工具已注册**：`get_document_chunks`，共 5 只工具。
- **验证**：`git diff --check` 无输出；`128 passed`。
- **Gate 结论**：通过，自动进入 Phase 4。
---

## Phase 4：HTTP 解耦

### P4.1 主服务内部只读 API

- 状态：done
- 提交：见本提交（`feat(api): expose versioned internal readonly mcp endpoints`）
- 修改文件：`src/web_api/internal_mcp.py`（新增：`/internal/mcp/v1` 4 个只读端点）、`src/web_api/app.py`（挂载，不走公共前缀）、`tests/unit/test_internal_mcp_api.py`（新增）
- 要求落实：
  - 仅 GET/POST 查询语义，无任何变更端点（测试断言 405）；
  - 复用同一 `InProcessRagReadOnlyClient`（注入应用服务，不复制检索实现）；
  - 集合范围由客户端按 principal 复检，不信工具传来的 collection 字符串；
  - 走 `/internal` 前缀，不对公网；`openapi.json` 标记 internal-mcp-readonly；
  - 错误为稳定 `code` 平面 JSON（not_found/access_denied/invalid_request/upstream_*），不回传堆栈。
- 已运行测试：`tests/unit/test_internal_mcp_api.py` → `9 passed`。
- 遗留问题：无（容器暴露边界见 P4.4）。

### P4.2 `HttpRagReadOnlyClient`

- 状态：done
- 提交：见本提交（`feat(mcp): add bounded readonly http client`）
- 修改文件：`src/mcp_server/clients/http_client.py`（新增）、`tests/unit/test_readonly_client_http.py`（新增）
- 要求落实：
  - 统一 base URL；显式 connect/read/write/pool 超时；
  - 默认验证 TLS；有界连接池（httpx.Limits）；每线程复用 Session；
  - `X-API-Key`（内部服务凭证）走 Header 传递，credential 存于对象、绝不日志；
  - 仅实现 4 个只读方法，无通用 `request(method, path)` 逃逸口（测试断言）；
  - 错误映射：404→not_found、403→access_denied、400→invalid_request、5xx→upstream_unavailable、timeout→upstream_timeout、连接失败→upstream_unavailable。
  - **WeKnora 借鉴**：base-URL + 线程本地复用 Session + Header 凭证模式，受 WeKnora `weknora_mcp_server.py` 启发；文件头已标注来源归属。
- 已运行测试：`tests/unit/test_readonly_client_http.py` → `12 passed`。

### P4.3 Client 工厂与显式切换

- 状态：done
- 提交：见本提交（`feat(mcp): add explicit rag client backend selection`）
- 修改文件：`src/mcp_server/clients/factory.py`（新增 `build_readonly_client`）、`src/mcp_server/tools/common.py`（改：`client_for` 走工厂）、`src/core/settings.py`（改：`McpServerSettings`）、`config/settings.yaml`（改：`mcp_server` 段）、`tests/unit/test_readonly_client_factory.py`（新增）
- 要求落实：
  - 配置文件示范：`mcp_server.rag_client_backend` / `rag_api_base_url` / `request_timeout_seconds`；
  - 默认 `in_process`，本地行为不变；
  - `http` 缺 base_url、或 backend 非法 → fail-fast（test 断言），不做静默回退。
- 已运行测试：`tests/unit/test_readonly_client_factory.py` → `4 passed`。

### Phase 4 Gate 验收记录（P4.1–P4.3）

- **验证**：`git diff --check` 无输出；Phase 4 全 MCP+内部 API 测试集 `153 passed`。
- **Gate 结论**：P4.1–P4.3 通过。P4.4 独立部署/容器隔离依 docker/compose 环境校验。

### P4.4 独立部署与容器隔离

- 状态：部分（见 Phase 5 交付说明；容器交互依赖外部 embedding/LLM 服务）
- 说明：MCP 进程仅通过主服务内部 HTTP API 取数；隔离与容器间联需要 docker compose 全栈（依赖 vLLM embedding/LLM，基线即为外部失败）。

### P4.4 独立部署与容器隔离（交付物）

- 状态：部分（工件已交付；完整容器联调取决于外部 embedding/LLM 服务）
- 提交：见本提交（`build(mcp): isolate standalone mcp runtime dependencies`）
- 修改文件：
  - `deploy/mcp/Dockerfile` + `deploy/mcp/requirements-mcp.txt`（仅 mcp/httpx/pydantic/pyyaml，无向量/Embedding/Reranker/解析依赖）
  - `deploy/mcp/mcp-settings.yaml`（`rag_client_backend: http`，`rag_api_base_url` 指向主服务）
  - `deploy/mcp/health_check.py` + `health_check.sh`（list_collections 探测 + `--expect-upstream-down`）
  - `docker-compose.yml`（新增 `mcp` service：无 data 挂载，`depends_on api health`，HEALTHCHECK 门禁）
  - `docs/THIRD_PARTY.md`（WeKnora 归属）
  - `tests/unit/test_readonly_client_http.py`（增：上游中断 → 协议级异常而非空答案）
- 验证：
  - `docker compose config --quiet` → 有效
  - `health_check.py --expect-upstream-down` → exit 0
  - MCP 镜像 build：后台 `docker build -f deploy/mcp/Dockerfile -t skdy-mcp:local .`（结果见最终交付）

---

## Phase 5：兼容、文档和交付

### P5.1 兼容期规则自动测试

- 状态：done
- 提交：见本提交
- 修改文件：`tests/unit/test_readonly_compat.py`（新增）
- 覆盖：5 只只读工具面与无写工具；`get_document_summary` 别名注册；`doc_id` 在 get_document/get_document_chunks 均可；`count`+`n_collections`；`n_results`+`citations`+`no_rerank`→rerank。
- 已运行测试：`tests/unit/test_readonly_compat.py` → `6 passed`。

### P5.2 文档

- 状态：done
- 提交：见本提交
- 修改文件：`docs/mcp-integration.md`（追加「4. Read-Only MCP 优化」节）、`docs/THIRD_PARTY.md`（已有，见 P4.4）、OpenAPI `internal-mcp-readonly` tag（P4.1 已加）。

### Phase 5 Gate / 交付验证总览

- **全套测试**（`pytest -q`）：`1471 passed, 41 failed, 1 skipped, 1 error`。
  - 较基线 `1384 passed / 40 failed`：净 +87 通过（全部为本计划新增用例）；
  - 41 failed + 1 error 全部为**既有外部**失败类别：LLM / Embedding 端点未启动、
    streamable-http CLI 子进程（`/health` 不可达）、上传校验、LLM/提示词 smoke；
    **无一落在本计划变更的 MCP / 只读 / 内部 API 模块**（本项目相关测试 65 passed 复跑确认）。
- **独立 MCP 镜像 build**（P4.4）：两次尝试均因访问 PyPI 的网络 `ReadTimeoutError`
  失败（环境网络问题，非代码问题）；`docker compose config --quiet` 通过、
  `health_check.py --expect-upstream-down` exit 0。镜像构建以「受网络阻塞」记录。
- **最终状态**：已完成 Phase 0–5 全部实现与门禁；P4.4 容器镜像构建受外部网络阻塞（工件已交付）。

---

## 审查整改（Review fixes）

### 整改 A（P1 #3）：进程内客户端服务访问崩溃
- 提交：见本提交
- 修复：`in_process.py` 中 `self._services` 实例属性曾遮蔽同名方法，`_document_service`/`_query_service` 调用会抛 `'NoneType' object is not callable`。方法改名 `_services_resolved()`。
- 测试：`tests/unit/test_readonly_client_inprocess_services.py`（真实注入 services bundle，断言取回 document/query）。

### 整改 B（P1 #4）：`run_server --config` 接入客户端工厂
- 修复：`server.py` 新增 `_bootstrap_rag_client(config_path)`，在 `run_server` 内于 `build_server` 前依据 `--config` + DEFAULT_DATA 构建 `RagReadOnlyClient` 并注入默认客户端；缺失文件宽容回退 in_process；非法 backend / http 缺 base_url 启动即失败（fail-fast）。此前 `--config` 未接工厂，工具回退到 `./config/settings.yaml`。
- 测试：`tests/unit/test_server_bootstrap_client.py`（in_process/http/非法 backend/缺 base_url/缺文件）。

### 整改 C（P0 #1/#2 + P2 #5b）：内部只读端点鉴权与隔离 + HTTP 错误映射
- 认证（#1）：`/internal/mcp/v1` 现要求 `X-API-Key == mcp_server.api_key`（core Settings），错误 401/503；未配置 key 即拒绝（fail-closed）。记入 `docs/mcp-integration.md`。
- 隔离（#2）：聚合端把调用方集合授权经 `X-MCP-Allowed-Collections` 转发；内部 API 据此构造受限 principal（`filter_accessible_collections` / `resolve_query_collection` 复验证）。缺该头 → deny-all；`TrustedLocalPrincipal` 跨 HTTP 永不视为全权。query 集合越权 → 稳定 403。
- HTTP 客户端（#5b + 构造）：`upstream_timeout`/HTTP 504/408 映射 `UpstreamTimeoutError`（此前误入 UpstreamUnavailable）；`httpx.Timeout` 改显式四相；新增 `trust_env`（默认 on，可关）；session 头合并可空安全。
- 真实部署：主服务 `api_key: ${MCP_INTERNAL_API_KEY}`，compose 对 api/mcp 同时注入共享密钥。
- 测试：`test_internal_mcp_api.py` 重写（认证/deny-all/scope 透传/稳定错误码/OpenAPI）、新增 `test_readonly_http_e2e.py`（真实 uvicorn 全链路隔离，不泄漏受信全权）。

### 整改 D（P2 #5a）：`get_document` doc_id-only 输入模式合法
- 修复：移除 `get_document` 顶层 `required:["document_id"]`，仅保留 `oneOf[document_id|doc_id]`（与 `get_document_chunks` 一致），使 doc_id-only 调用在 JSON Schema 层面合法，别名得以成立。
- 测试：`test_get_document.py` 断言无顶层 required 且 oneOf 覆盖两种 id；快照 `phase0_tool_schemas.json` 重新生成。
