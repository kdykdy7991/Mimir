# Task 02 开发提示词：MCP 公共契约与能力发现

将下面整段提示词交给负责 Task 02 的开发人员执行。

---

你正在仓库 `/home/hello/workspace/SKDY-RAG-SERVER` 中执行“MCP 知识基础设施路线”的第二个任务：
**MCP 公共契约与能力发现**。

## 一、开始前必须阅读

请完整阅读以下文件，不得只读摘要：

1. `docs/adr/0001-mcp-server-only-rag-boundary.md`
2. `docs/plans/weknora-mcp-roadmap/README.md`
3. `docs/plans/weknora-mcp-roadmap/02-mcp-public-contracts-and-capabilities.md`
4. `docs/plans/weknora-mcp-roadmap/01-architecture-contract-and-retrieval-baseline.md`
5. `docs/contracts/mcp-readonly-v1.md`
6. `docs/implementation-status/readonly-mcp-optimization.md`
7. `docs/mcp-integration.md`
8. `docs/prd-mcp-api-key-collection-access.md`

同时检查 Task 01 的真实产物：

- `scripts/mcp_contract_inventory.py`
- `tests/fixtures/mcp_contract/readonly_v1_inventory.json`
- `tests/unit/test_mcp_contract_v1_snapshot.py`
- `tests/fixtures/retrieval_golden/`
- `scripts/run_retrieval_eval.py`
- `scripts/eval_gate.py`

## 二、当前基线

Task 01 已完成并通过 Gate，提交如下：

- `153e776`：架构 ADR；
- `dd82898`：现有只读 MCP 契约清单；
- `da6c2cb`：Golden Set；
- `cb2c171`：确定性离线评测器；
- `e315231`：四模式 CI 基线和回归 Gate。

当前 MCP 真实注册表有且仅有 5 只工具：

1. `query_knowledge_hub`
2. `list_collections`
3. `get_document`
4. `get_document_summary`
5. `get_document_chunks`

本任务不得破坏这 5 只工具的现有名称、必填字段、oneOf、兼容别名、默认值、授权或错误语义。

Task 01 的 CI 检索基线使用确定性 Hash Embedding；真实 Embedding/Rerank 的 release profile 尚未记录，
这是已登记的发布前事项。本任务不负责调整检索质量或补做真实模型基线。

## 三、不可突破的范围

本任务只实现公共契约与能力发现，不提前进入 Task 03 或 Task 04。

禁止：

- 新增 `list_documents`、`get_chunk`、`search_chunks` 等后续工具；
- 修改 Dense、BM25、Hybrid、RRF 或 Rerank 行为；
- 修改分块、索引、父子 Chunk 或多 collection 检索；
- 引入查询改写、HyDE、LLM loop、Agent Core、MCP Client；
- 改变旧工具已有字段的必填性、类型、默认值或错误文案；
- 将 MCP SDK、Web DTO、Chroma、BM25 或 LLM 类型引入 application 公共契约；
- 为了让快照通过而弱化 Task 01 的契约门禁；
- 推送前自动开始 Task 03。

允许：

- 新增 application 层传输无关契约；
- 新增集中 MCP mapper，并将旧工具内部格式化逻辑逐步委托给它；
- 为旧工具追加完全向后兼容的可选字段；
- 新增统一预算配置和校验器；
- 新增只读 capabilities MCP Resource；若当前 SDK 确实无法稳定支持，再退回只读工具；
- 更新契约快照、文档和测试。

## 四、工作区保护和实施纪律

开始前执行并记录：

```bash
git status --short
git branch --show-current
git rev-parse HEAD
```

要求：

- 现有改动属于用户，不得覆盖、回退或混入；
- 禁止 `git reset --hard`、`git checkout --`、`git clean`；
- 不得使用 `git add -A`；每次只暂存本原子任务的精确文件；
- 每个 02.x 独立测试、独立提交；
- 不推送远端；
- 每批提交前运行 `git diff --check`；
- 将 `docs/plans/weknora-mcp-roadmap/02-mcp-public-contracts-and-capabilities.md` 状态改为“进行中”，持续追加实施记录；
- 全部 Gate 通过后改为“完成”，然后停止。

## 五、先做代码现状审计

实施前必须基于当前代码确认并记录：

1. `src/mcp_server/clients/models.py` 中现有 transport-neutral dataclass 的实际使用点；
2. 5 只工具各自 `_format`/structured content 的重复与差异；
3. `ProtocolHandler` 对 output schema、structured content 和 `CallToolResult` 的处理；
4. 当前 MCP SDK 是否支持静态/模板 Resource、Resource Link 和 resource read handler；
5. `Settings`、`McpServerSettings` 中现有上限及环境覆盖方式；
6. top-k、query length、page/page-size 上限目前有哪些重复常量；
7. `ReadonlyClientError`、HTTP 错误映射和工具级错误现状；
8. OpenAPI 是否会复用本次 application 契约；如果不复用，不要无意义刷新 OpenAPI；
9. capability 内容怎样从真实注册表和真实配置生成，避免维护第二套事实源。

任务书与当前实现不一致时，以代码为事实，在实施状态中记录偏差，不擅自扩大范围。

## 六、按严格顺序实施

### 02.1 应用层传输无关契约

目标：在 application 层定义未来读取和检索工具共享的 v1 契约，不能依赖 MCP/Web/基础设施。

建议位置：

```text
src/application/contracts/
  __init__.py
  evidence.py
```

可以根据仓库现有结构调整位置，但必须保持 application 所有权。

至少定义以下概念：

#### EvidenceScores

固定字段：

- `dense: float | None`
- `sparse: float | None`
- `fusion: float | None`
- `rerank: float | None`

未执行的阶段必须序列化为 `null`，不得省略或伪造为 0。

#### SourceLocator

兼容现有定位类型，至少表达：

- `kind`
- `page`
- `heading`

不得包含绝对磁盘路径。

#### EvidenceV1

必填语义：

- `collection_id`
- `document_id`
- `chunk_id`
- `content_type`
- `source_locator`
- `scores`
- `matched_queries`

为后续保留的可选字段：

- `document_version`
- `chunk_version`
- `parent_chunk_id`
- `title`
- `content`
- `content_preview`
- `heading_path`
- `asset_ids`
- `indexed_at`

不要为了填满新契约而从旧数据猜测版本、父块或资产。

#### EvidenceFilterV1

至少支持：

- collection IDs；
- document IDs；
- tag IDs；
- `tag_operator=and|or`；
- folder ID；
- `include_descendants`；
- file types；
- content types；
- source types；
- updated after/before。

契约规则：

- 缺失表示不限制；
- 显式空数组非法；
- `include_descendants` 只有 folder ID 存在时才允许；
- 时间必须带时区；
- 输入集合去重后保持确定顺序；
- 不在本任务实现实际过滤查询。

#### Warning 和分页/预算结果

Warning code 至少包括：

- `truncated`
- `rerank_degraded`
- `legacy_metadata_missing`
- `partial_collection_failure`

分页/预算契约至少能表达：

- page 或 cursor 信息；
- 返回数量；
- 是否有下一页；
- 是否因字符或结果预算截断。

实现要求：

- 类型不可变，或具备等价的不可意外修改保证；
- 提供单一、确定性的 JSON-safe 序列化入口；
- datetime 输出带时区 ISO 8601；
- unknown optional field 的读取策略明确；
- 添加测试证明 application 契约不导入 MCP/Web/Chroma/BM25 类型。

旧 `src/mcp_server/clients/models.py` 不能在本提交中被破坏。可以先保留并在 02.2 中适配，也可以安全 re-export，
但必须证明现有 Client 和工具不变。

建议提交：

```text
refactor(query): add transport-neutral evidence contracts
```

### 02.2 集中 MCP DTO 与兼容映射

目标：建立单一 mapper，将 application 契约转换为 MCP structured content，并消除工具间新契约映射的复制。

建议新增：

```text
src/mcp_server/presentation/evidence_mapper.py
```

要求：

- application 层不能导入该 mapper；
- mapper 不访问数据库、Client、Embedding 或检索服务；
- mapper 只做 DTO、兼容字段、预览和安全清洗；
- 旧工具的现有响应必须逐字或结构兼容；
- 新字段只能可选追加；
- 旧 `score` 可以继续保留，但新 `scores` 必须明确多阶段语义；
- 不能用旧单一 score 猜测所有 dense/sparse/fusion/rerank 值；
- 绝对路径、Secret、Authorization Header、traceback 不得进入输出；
- 空 Evidence、降级、旧数据字段缺失均有测试。

本任务不要求强行把所有旧工具一次性重写。如果迁移某工具会造成快照破坏，可让 mapper 先服务新增 v1 契约，
旧 formatter 通过兼容 wrapper 委托；必须在状态文档说明暂留点，并确保 Task 04 能删除双实现。

建议提交：

```text
refactor(mcp): centralize evidence response mapping
```

### 02.3 统一预算、分页和请求限制

目标：把散落的 query length、top-k、page-size、返回字符数、查询数量限制收敛到单一配置来源和校验器。

至少包含：

- query 最大长度；
- top-k 默认值和最大值；
- page-size 默认值和最大值；
- Evidence 最大条数；
- structured content 最大字符数；
-单 Evidence 正文/预览最大字符数；
- 为 Task 05 预留 alternate query 数量与总字符上限，但本任务不开放该输入。

要求：

- 配置由 `Settings`/MCP settings 统一持有；
- 使用 Pydantic 启动期校验，非法配置 fail-fast；
- 工具 Schema 的 default/min/max 从同一常量或构建函数生成；
- 运行时校验与 Schema 一致；
- 超限要么返回稳定 invalid_request，要么在输出层显式 `truncated`；两者使用场景必须固定；
- 不能无提示截断；
- 不能为了兼容而改变现有 5 工具的公开默认值或上限。

需要新增并稳定映射错误：

- `invalid_request`
- `not_found_or_not_accessible`
- `upstream_timeout`
- `upstream_unavailable`
- `rate_limited`
- `overloaded`

后两项本任务只建立契约和异常类型，不实现实际限流；不得错误映射成 upstream unavailable。

建议提交：

```text
feat(mcp): enforce bounded evidence responses
```

### 02.4 能力发现

优先实现只读 MCP Resource：

```text
rag://server/capabilities
```

必须先用当前安装的 MCP SDK 写最小验证，确认：

- list resources 可发现；
- read resource 可返回 JSON；
- stdio 和 streamable-http 都使用同一 Server 注册结果；
-不会改变 5 只工具的数量和快照。

capabilities 至少返回：

- `server_name`
- `contract_version`
- 实际注册工具及其生命周期；
- 支持的 transport；
- 当前支持的 retrieval modes；
- 当前支持的 filters；
- query/top-k/page-size/响应预算；
- 当前内容类型；
- `parent_child_chunks=false`
- `source_assets=false`
- `multi_collection_search=false`
- `alternate_queries=false`
- `write_tools=false`

事实源要求：

- 工具列表来自真实注册表；
- 限制来自真实 Settings；
- 支持状态由实现/注册情况产生；
- 不维护一份容易漂移的手写工具名单；
- 不返回 API Key、内部 URL、数据库路径或 Provider Secret。

如果 MCP SDK 确实无法可靠支持 Resource，才允许增加 `get_server_capabilities` 工具。选择工具会使数量从 5 变 6，
必须记录 SDK 证据、更新契约快照并说明兼容性。不能因为实现方便直接跳过 Resource 调研。

建议提交：

```text
feat(mcp): expose versioned server capabilities
```

### 02.5 契约快照、文档和最终 Gate

完成以下工作：

- 更新 MCP Resource/capability 快照；
- 确认旧 5 工具 Schema 无未批准变化；
- 如果 REST API 未直接复用本次 DTO，不要无意义刷新 OpenAPI；
- 如果共享 DTO 影响 OpenAPI，则重新导出并更新 Web 生成类型；
- 增加 scores-null、时间、路径清洗、Warning/Error、预算和 unknown optional field 测试；
- 更新 `docs/contracts/mcp-readonly-v1.md`，说明 capabilities 和 v1 Evidence；
- 更新 `docs/mcp-integration.md` 顶部仍写“三个工具”的过时描述；
- 更新 Task 02 状态文档，列出提交、文件、测试、偏差、限制和回滚。

建议提交：

```text
test(contract): freeze evidence and capabilities v1
```

## 七、测试要求

至少覆盖：

1. application 契约序列化、不可变性和依赖边界；
2. scores 的 null 语义；
3. Filter 空数组、重复值、非法 tag operator、无时区 datetime；
4. Warning/Error 全枚举及 JSON 输出；
5. 绝对路径、Secret、Header 和 traceback 清洗；
6. query/top-k/page-size/字符预算边界值与越界；
7. 非法配置启动期失败；
8. capabilities 与真实工具注册表和 Settings 一致；
9. MCP Resource 的 list/read；
10. stdio 与 streamable-http 的 capability 一致；
11. Task 01 MCP inventory `--check`；
12. 全部既有 MCP 单元和集成测试；
13. Task 01 Golden Set 基线 Gate，证明未改变检索行为；
14. `git diff --check`。

建议至少运行：

```bash
.venv/bin/python scripts/mcp_contract_inventory.py --check
.venv/bin/python -m pytest $(find tests -name '*mcp*.py') -q
.venv/bin/python -m pytest tests/unit/test_architecture_boundary.py -q
.venv/bin/python -m pytest tests/unit/test_eval_gate.py tests/integration/test_eval_baseline_gate.py -q
.venv/bin/python -m scripts.export_openapi --check
git diff --check
```

根据实际新增文件补充更精确的测试命令。不要只写“测试通过”，必须记录真实命令、通过数、skip 和失败分类。

## 八、完成 Gate

只有同时满足以下条件，Task 02 才能标记完成：

- application 层已有稳定、传输无关的 Evidence/Filter/Warning/分页预算契约；
- application 层没有 MCP/Web/Chroma/BM25 依赖；
- MCP 映射集中且安全清洗有测试；
- 所有限制有单一配置来源，Schema 与运行时一致；
- rate_limited 和 overloaded 已有独立错误契约，但未假装实现限流；
- capabilities 可机器读取，并与真实注册表/配置一致；
- 旧 5 工具名称、必填、oneOf、别名、默认值、授权和错误语义兼容；
- Task 01 inventory check 通过；
- Task 01 Golden Set Gate 通过，检索行为无变化；
- 没有新增检索、读取、父子分块、多查询或多库功能；
- Task 02 状态记录完整；
- 工作区原有修改未被覆盖；
- `git diff --check` 通过。

## 九、停止与交付报告

Task 02 Gate 通过后立即停止，不要启动 Task 03，不要推送远端。

最终报告必须包含：

1. 02.1–02.5 完成状态；
2. 原子提交列表；
3. 修改文件；
4. 最终 application 契约；
5. 旧 MCP 兼容证明；
6. capability 输出示例；
7. Resource 或工具方案选择及证据；
8. 配置上限及环境覆盖方式；
9. 测试命令和真实结果；
10. Golden Set Gate 结果；
11. 环境限制与遗留问题；
12. 回滚方法；
13. `git status --short`。

---

本任务的核心原则：**先统一知识证据的语言和边界，再增加新工具；Task 02 只能定义和发现能力，不能提前实现能力。**
