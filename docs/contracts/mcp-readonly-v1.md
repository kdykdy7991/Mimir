# MCP 只读工具契约 v1（mcp-readonly-v1）

- 状态：Accepted（2026-09-15 冻结）
- 适用范围：SKDY-RAG MCP Server 暴露给外部 Agent 的全部工具
- 架构边界：[`docs/adr/0001-mcp-server-only-rag-boundary.md`](../adr/0001-mcp-server-only-rag-boundary.md)
- 机器事实源（权威 Schema）：
  [`tests/fixtures/mcp_contract/readonly_v1_inventory.json`](../../tests/fixtures/mcp_contract/readonly_v1_inventory.json)
- 生成器：[`scripts/mcp_contract_inventory.py`](../../scripts/mcp_contract_inventory.py)
- 漂移门禁：[`tests/unit/test_mcp_contract_v1_snapshot.py`](../../tests/unit/test_mcp_contract_v1_snapshot.py)

> 本文档是**面向集成方的说明书**；工具的 `input_schema` / `output_schema`
> 以机器快照为唯一事实源，快照由服务器真实注册结果
> （`src.mcp_server.server._register_default_tools`，stdio 与
> streamable-http 共用同一注册表）生成，禁止手抄第二套。

## 1. 契约总则

1. **全部工具只读**。契约 v1 不含任何 create/upload/update/delete/import/
   chat/agent 类工具；写操作属于管理 REST/Web 面，不经过 MCP。
2. **返回证据，不返回答案**。`query_knowledge_hub` 返回带身份、分数、定位的
   原始证据 chunk；服务端不做问题理解、查询改写、答案合成（ADR §2/§3.1）。
3. **无状态、单轮**。服务端不持有调用方会话、对话历史或长期记忆。
4. **两种错误面**：
   - **工具级错误**：`CallToolResult.is_error=true`，正文为固定英文文案
     （输入校验、授权、资源不存在等可预期错误）；
   - **协议级错误**：handler 未捕获异常直接传播为 JSON-RPC error
     （如检索基础设施故障 `knowledge retrieval failed: <ExceptionType>`），
     **绝不用空结果伪装成功**。
5. **存在性不泄露**：文档不存在与存在但无权限返回**同一句**
   `document not found or not accessible`；无权限 collection 的名称、计数、
   存在性均不暴露。
6. **不回传敏感环境信息**：错误与结果不含本地磁盘绝对路径、密钥、堆栈。

## 2. 传输与认证

| 调用路径 | 认证主体 | 说明 |
| --- | --- | --- |
| stdio MCP（本地子进程） | `TrustedLocalPrincipal`（全部 collection） | 本机管理/CLI 形态 |
| streamable-http MCP | API Key Bearer → `AccessPrincipal`（collection 白名单） | 外部 Agent 形态；缺认证 fail-closed |
| `InProcessRagReadOnlyClient` | 复用同一 principal 模型 | Web/REST 与 MCP 共用应用层，无网络 |
| `HttpRagReadOnlyClient` | Bearer | 独立部署形态的内部 HTTP 客户端 |

Collection 授权规则（详见 `docs/prd-mcp-api-key-collection-access.md`）：

- 显式传入的 `collection` 必须在凭据授权集合内；
- 单库授权可省略 `collection`（自动选择）；多库授权省略时返回工具级错误
  `'collection' is required when this credential can access multiple knowledge bases`；
- 授权在构造 embedding / 向量库 / BM25 / reranker **之前**执行。

## 3. 工具清单（5 只）

| 工具 | 类别 | 生命周期 | 分页 |
| --- | --- | --- | --- |
| `list_collections` | collection 发现 | long_term | — |
| `query_knowledge_hub` | 证据检索（原始 chunk） | long_term | 单窗 top-k，1..50（默认 10） |
| `get_document` | 文档元数据 | long_term | — |
| `get_document_summary` | 文档元数据（**兼容别名工具**） | compat_alias | — |
| `get_document_chunks` | chunk 分页读取（有界正文） | long_term | page≥1，page_size 1..50（默认 20） |

所有工具同时在 stdio 与 streamable-http 上可用，并在两种只读客户端上有对应方法。
每只工具的完整 JSON Schema、错误表与授权说明见机器快照。

### 3.1 `list_collections`

- 入参：无（`additionalProperties: false`）。
- 返回：`count` + `collections[]`（`name` 必填；`description`/
  `document_count`/`chunk_count` 可空——统计存储不可用时计数为 `null`，
  **绝不编造 0**），仅含凭据授权且实际存在的库；无结果时正常返回空列表，
  不是错误。
- 兼容别名：输出字段 `n_collections` → `count`。

### 3.2 `query_knowledge_hub`

- 入参：`query`（必填，1..2000 字符）、`collection`、`top_k`
  （整数 1..50，默认 10）、`rerank`（布尔，默认 true）。
- 返回：`query`、`collection`、`count`、`evidence[]`、`diagnostics`。
  每条 evidence 含 `rank`、`chunk_id`、`document_id`、`source`、`text`，
  可选 `title`、`page`、`score`；`source_type` 经 diagnostics/score 来源体现
  dense/sparse/fusion/rerank 分支。
- 诊断：`diagnostics.degraded`（布尔）+ `reasons[]` + `trace_id`。
  检索**降级**（如某一路失败、reranker 不可用）是成功响应上的显式信号，
  与协议级基础设施错误严格区分。
- 工具级错误：空 query、超长、collection 越权、多库凭据缺 collection。
- 协议级错误：embedding/向量库/BM25 整体失败
  → `knowledge retrieval failed: <ExceptionType>`。
- 兼容别名：入参 `no_rerank`（deprecated；与 `rerank` 同时出现时
  **`rerank` 优先**）；输出 `n_results` → `count`、`citations` → `evidence`。

### 3.3 `get_document`

- 入参（oneOf）：`document_id`（规范名）或 `doc_id`（兼容别名），
  二者至少一个；无顶层 `required`，否则只传 `doc_id` 的旧客户端会被判非法。
- 返回单文档元数据：`document_id`、`title`、`summary`、`tags`、`source`、
  `doc_type`、`chunk_count`（不含无界正文）。
- 工具级错误：两者都缺 → `'document_id' is required and must be a non-empty string`；
  未知 ID 或越权 → `document not found or not accessible`（同形）。
- 兼容别名：输出 `doc_id` → `document_id`、`doc_type` → `document_type`、
  `source_path` → `source`。

### 3.4 `get_document_summary`（兼容别名工具）

- 与 `get_document` **共享同一 handler、同一输出 Schema**，仅工具名与入参
  形式不同：`doc_id` 为必填。保留用于已发布的旧客户端；生命周期标记
  `compat_alias`，下线前至少保留一个发布窗口的废弃通告期。

### 3.5 `get_document_chunks`

- 入参（oneOf）：`document_id` 或 `doc_id`；`page`（整数，≥1，默认 1）、
  `page_size`（整数 1..50，默认 20）。
- 返回：`document_id`、`page`、`page_size`、`total`、`has_next`、
  `chunks[]`（chunk 必填 `chunk_id`、`index`，含该页有界正文）。
- 顺序是**显式稳定顺序**，与向量库自然返回序解耦：
  `chunk_index → 内嵌序号（chunk id 中的 _NNNN_）→ chunk id`。
- 工具级错误：ID 缺失、`page`/`page_size` 非整数
  （`'page' and 'page_size' must be integers`）、
  `page_size must be between 1 and 50`、`page must be >= 1`、
  未知/越权文档同形 not-found。

## 4. 兼容别名与保留策略

| 别名 | 规范名 | 位置 | 保留计划 |
| --- | --- | --- | --- |
| `no_rerank` | `rerank` | query 入参 | compatibility window；两者并存时 `rerank` 胜 |
| `n_results` | `count` | query 输出 | compatibility window |
| `citations` | `evidence` | query 输出 | compatibility window |
| `n_collections` | `count` | list_collections 输出 | compatibility window |
| `doc_id` | `document_id` | 文档类工具入参/输出 | compatibility window |
| `doc_type` | `document_type` | 文档类工具输出 | compatibility window |
| `source_path` | `source` | 文档类工具输出 | compatibility window |
| 工具 `get_document_summary` | 工具 `get_document` | 工具名 | compat_alias：下线前 ≥1 个发布窗口通告 |

别名删除、必填字段收紧、`oneOf` 分支变化、新增枚举/常量约束、工具增删，
均属**破坏性契约事件**，必须：更新机器快照与本文档、在变更说明中写明兼容性、
通过契约快照测试（直接改快照无法绕过生成器交叉校验）。

契约 v1 冻结时刻，全部工具 Schema 中**不存在 `enum`/`const` 约束**；
任何新增枚举都会被漂移测试捕获，需要显式评审。

## 5. 显式不属于本契约的能力

- 答案生成、追问、查询改写/扩展、多步规划、工具循环（ADR §3.1）；
- 任何写操作与管理操作（上传、删除、配置、任务治理）；
- 会话、对话历史、长期记忆；
- 大正文/图片的无界内联（未来通过 MCP Resources 按需读取，见路线图 Phase 8）。

## 6. 快照与漂移门禁

```bash
# 校验已提交快照与当前注册一致（退出码 1 = 漂移）
.venv/bin/python scripts/mcp_contract_inventory.py --check

# 有意的契约变更：评审后重新生成
MCP_REGENERATE_V1=1 .venv/bin/python -m pytest tests/unit/test_mcp_contract_v1_snapshot.py
# 或
.venv/bin/python scripts/mcp_contract_inventory.py \
  --output tests/fixtures/mcp_contract/readonly_v1_inventory.json
```

`pytest` 门禁包含两层：

1. **整体快照深比较**（工具名、描述、完整 input/output Schema 与策展元数据）；
2. **定点漂移断言**：工具集合、必填字段、`oneOf` 分支、兼容别名属性、
   数值上下界/默认值、枚举/常量集合（当前为空）——即使生成器与快照被同时
   修改，针对真实注册表的断言仍会失败。

快照重复生成必须无 diff（排序键固定、`ensure_ascii=False`、末尾换行）。
