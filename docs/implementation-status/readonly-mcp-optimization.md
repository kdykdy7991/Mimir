# Readonly MCP Optimization — Implementation Status

> 方案：`docs/plan-2026-09-10-weknora-readonly-mcp-optimization.md`
> 目标分支：`feature/weknora-inspired-optimizations`
> WeKnora 固定参考提交：`3e6010e7cd3937f289cc1dbadc829e71eb1163f4`
> 本文件在开发过程中持续更新，不搞「最后一次性补齐」。

## 阶段总览

| Phase | 标题 | 状态 | 提交数 |
| --- | --- | --- | --- |
| Phase 0 | 固定基线和边界 | done | 2 |
| Phase 1 | 建立只读 Client 边界 | in-progress | 0 |
| Phase 2 | 规范现有工具 | todo | — |
| Phase 3 | 新增文档 Chunk 分页 | todo | — |
| Phase 4 | HTTP 解耦 | todo | — |
| Phase 5 | 兼容、文档和交付 | todo | — |

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