# 任务 01：架构边界、现状契约与检索基线

> 状态：进行中（2026-09-15 开始）  
> 前置：现有 DocReader、只读 MCP、知识管理和 Trace 相关测试可运行  
> 后继：任务 02

## 0. 实施状态流水（持续追加）

### 2026-09-15 启动与工作区保护

- 分支：`feature/weknora-inspired-optimizations`；HEAD：`e59a232 allow startup without embedding provider`；无 stash。
- 启动时 `git status --short`（用户既有修改，本任务全程不触碰、不混入提交）：
  - `M README.md`、`M docs/plan-2026-07-31-m2-batch3.md`、`M docs/web-api-onboarding.md`
  - `D docs/handoff-2026-07-31-m2-batch2.md`、`D docs/handoff-2026-07-31-m2-batch3.md`、
    `D docs/handoff-2026-08-03-m3-batch1.md`、`D docs/handoff-2026-08-03-m3-batch2.md`、
    `D docs/handoff-2026-08-03-m4-release.md`、`D docs/plan-2026-08-27-docbench-optimizations.md`
  - `?? docs/plan-2026-09-15-weknora-mcp-capability-roadmap.md`、`?? docs/plans/`
- 保护措施：仅用 `git add <精确文件>` 提交本任务新增/修改文件；不使用 `git add -A`、
  `git reset --hard`、`git checkout --`、`git clean`；不推送远端；每个原子任务单独验证、单独提交。

### 只读现状审计结论（2026-09-15，基于 HEAD `e59a232` 实际代码）

1. **实际注册 MCP 工具 = 5 只**（`src/mcp_server/server.py::_register_default_tools`）：
   `query_knowledge_hub`、`list_collections`、`get_document`、`get_document_summary`（兼容别名）、
   `get_document_chunks`。stdio 与 streamable-http 共享同一注册表（`server.py:200-217`）。
   三条调用路径：stdio 子进程、HTTP（Bearer 认证）、in-process（`InProcessRagReadOnlyClient`，
   MCP handler 与独立部署 HTTP client 共用应用层）。
2. **检索链路**：`query_knowledge_hub` → client `query_knowledge` → `QueryService.search`
   （`src/application/services/query_service.py:243`，支持 `mode=hybrid|dense|sparse`）→
   `HybridSearch`（query_processor 纯规则分词 → DenseRetriever + SparseRetriever →
   RRF k=60 融合 → 后置过滤 → top-k）；rerank 在应用服务外套 `RerankerStage`（MCP client
   `_build_search`，`in_process.py:279-292`）。
3. **确定性现状**：RRF 同分按 chunk_id 升序（`fusion.py`）；BM25 同分按 chunk_id
   （`bm25_indexer.py`）；**dense 单路同分信任 Chroma 返回顺序，无 chunk_id tie-break**——
   评测器将在指标层对同分行按 chunk_id 稳定归一化（不改生产行为，记录为已知差异）。
4. **无 Agent 能力**：无 Agent Core/ReAct/Planner/Reflection、无工具循环、无最终答案生成
   （`core/response/*` 是纯 shape 映射）、无会话/长期记忆、无隐式查询改写
   （`QueryProcessor` 纯规则；`ProcessedQuery.expanded` 无业务写入点）。
5. **MCP Client 用法唯一例外**：`src/web_api/mcp_connection.py`（管理面一次性连接自检：
   initialize → tools/list → 一次 list_collections，URL 仅来自服务端配置、硬超时、无循环），
   已作为 ADR §3.1 精确白名单豁免；`src/mcp_server/` 零 `mcp.client` 导入。
6. **固定单轮模型阶段**：Rerank（none/cross_encoder/llm，top_m=30，异常整体回退）、
   ChunkRefiner(C5)/MetadataEnricher(C6)/ImageCaptioner(C7)/ImageClassifier 摄取期单轮
   （`use_llm` 均默认关，均有 fallback）、docreader/VLM OCR 整文档单次解析。
7. **既有契约快照**：`tests/unit/test_mcp_contract_snapshot.py` +
   `tests/fixtures/mcp_contract/phase0_tool_schemas.json`（真实注册生成、
   `MCP_REGENERATE_SNAPSHOT=1` 重生成、整体深比较）；另有 `test_mcp_readonly_invariants.py`
   （5 项只读不变量）、`test_readonly_compat.py`（旧客户端兼容）。
8. **离线评测设施现状**：无现成检索 Golden Set；`scripts/evaluate.py` 是 stub；
   无 conftest.py；无确定性可语义检索的 embedding（测试 FakeEmbedding 多用受
   PYTHONHASHSEED 影响的内建 `hash()`）；BM25/Chroma 双索引可在 tmp 目录确定性重建
   （范本 `tests/integration/test_hybrid_search.py`、`test_bm25_consistency.py`）。
   环境：无 sentence-transformers/torch；本机 vLLM embedding `localhost:8003` 在线、
   LLM `localhost:8000` 不通；rerank 配置为 none。
9. **任务书/旧文档与现状差异**（记录，不在本任务扩大范围修复）：
   - `docs/mcp-integration.md` 文首仍写“三个 RAG 工具”（§4 已写 5 只）——遗留过期描述；
   - `rerank.backend: none` 时 MCP 请求 `rerank=true`（默认）仍会追加
     `reranker unavailable; retrieval degraded`（`in_process.py:326-328`）——现存行为怪癖；
   - dense 单路缺 chunk_id tie-break（见第 3 条）。

### 01.1 建立正式 ADR

- 状态：done
- 提交：见本提交（`docs(adr): define mcp-server-only rag boundary`）
- 新增文件：
  - `docs/adr/0001-mcp-server-only-rag-boundary.md`（背景/定位/决策/职责边界/允许模型阶段/
    永久排除项/正反例/安全原则/评审约束/后果/复审条件/现状盘点）
  - `tests/unit/test_architecture_boundary.py`（15 项：src 顶层包精确白名单参数化、
    禁词模块深度 1-2 精确名检查、`mcp.client` 仅白名单文件可用、白名单文件存在性、
    `src/mcp_server/` 不得出现 client SDK）
- 设计要点：只做**精确路径/白名单**检查，不扫描第三方依赖、docs、.github、tests，
  不因普通词汇误报；与既有 `test_mcp_readonly_invariants.py` 不重复。
- 测试命令与结果：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_architecture_boundary.py -q
  # 15 passed in 0.04s
  git diff --check   # 无输出
  ```
- 遗留问题：无。

## 1. 目标

在改变线上行为前冻结系统职责、现有 MCP 兼容面和检索质量基线。后续任何功能必须能回答：是否越过
Agent 边界、是否破坏旧客户端、是否改善或至少不恶化检索。

## 2. 非目标

- 不新增 MCP 工具；
- 不修改检索、排序、分块或索引行为；
- 不引入 Ragas、在线 LLM Judge 或新基础设施；
- 不把用户私有文档正文提交到仓库。

## 3. 原子任务

### 01.1 建立 ADR

新增 `docs/adr/0001-mcp-server-only-rag-boundary.md`，至少记录：背景、决策、允许的固定单轮模型阶段、永久排除项、
MCP/REST/内部服务边界、正反例、后果和复审条件。

自动检查：新增轻量文档测试或架构测试，禁止生产代码新增 `agent`、`planner`、`conversation`、`memory`
顶层模块；现有历史命名如有冲突须用白名单，不做机械改名。

提交：`docs(adr): define mcp-server-only rag boundary`

### 01.2 导出现有 MCP 清单

新增 `docs/contracts/mcp-readonly-v1.md` 与机器可读快照，记录当前工具名、Schema、别名、错误、授权、Client
后端和是否返回原始证据。快照必须由服务器注册结果生成，不能手抄两套事实源。

提交：`test(mcp): freeze current readonly contract inventory`

### 01.3 定义 Golden Set v1

建议目录：

```text
tests/fixtures/retrieval_golden/
  schema.json
  cases.jsonl
  README.md
```

字段包含 case ID、query、collection IDs、relevant document/chunk IDs、可接受来源定位、filters、tags。
至少准备精确关键词、中文语义、多栏 PDF、表格、OCR、无答案六类样本。若稳定 Chunk ID 依赖夹具入库，
提供固定 seed 脚本。

提交：`test(retrieval): add versioned golden-set corpus`

### 01.4 实现离线评测器

新增 `scripts/run_retrieval_eval.py`，通过应用层查询接口运行，不经 Web 或 MCP 网络层。参数至少包括数据目录、
cases、mode、top-k、rerank、输出路径。结果写 JSON，报告生成器写 Markdown。

指标：Recall@K、Precision@K、MRR、nDCG、document/chunk hit rate、no-answer accuracy、P50/P95、平均结果字符数。
单 case 报告保存期望、实际 ID、rank 和安全的文本摘要，不保存完整敏感正文。

提交：`feat(eval): add deterministic retrieval evaluation runner`

### 01.5 生成基线并接 CI

生成 dense、sparse、hybrid、hybrid+rerank 四份快照。CI 默认运行无外部模型依赖的确定性子集；需要真实模型的
完整基线作为显式命令和发布门禁。比较器对指标下降、case 消失、契约变化返回非零退出码。

提交：`test(eval): record retrieval baseline and regression gate`

## 4. 关键实现约束

- 固定排序：分数相同使用稳定 Chunk ID 作为 tie-break；
- 计时使用 monotonic clock；性能阈值允许按环境 profile 配置；
- 无答案准确率与基础设施失败分开统计；
- 缺少 Embedding/Rerank Provider 时明确 skip/degraded，不能记为零召回；
- Golden Set 的 schema version 必填。

## 5. 测试与验收

- 现有 MCP 契约测试全绿；
- 快照重复生成无 diff；
- 同一固定索引连续运行两次，ID 排名一致；
- 构造一个排序回归，比较器必须失败；
- 构造一个上游异常，不得被计为 no-answer；
- `git diff --check` 通过。

## 6. Gate 与回滚

Gate：ADR 审核通过；契约快照可重复；Golden Set 和四模式基线存在；比较器可阻断人工制造的回归。

回滚：本任务仅新增文档、夹具和评测工具；删除新增入口不影响生产运行。不得在本任务中调整现有阈值以
“修复”真实回归。

