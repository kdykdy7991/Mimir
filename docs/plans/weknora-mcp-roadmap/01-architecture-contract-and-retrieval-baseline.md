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
- 提交：`153e776`（`docs(adr): define mcp-server-only rag boundary`）
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

### 01.2 导出现有 MCP 清单

- 状态：done
- 提交：见本提交（`test(mcp): freeze current readonly contract inventory`）
- 新增文件：
  - `scripts/mcp_contract_inventory.py`（清单生成器；离线、不构造检索栈）
  - `tests/fixtures/mcp_contract/readonly_v1_inventory.json`（机器快照）
  - `docs/contracts/mcp-readonly-v1.md`（人类契约：总则/传输认证/5 工具/
    兼容别名保留策略/显式排除能力/重生成门禁）
  - `tests/unit/test_mcp_contract_v1_snapshot.py`（11 项，两层漂移门禁）
- 事实源策略：`name/description/input_schema/output_schema` 直接抽自真实注册表
  `_register_default_tools`（stdio/HTTP 共用）；策展元数据（别名、错误、授权、
  后端矩阵、生命周期）在生成器内维护，其中**涉及 schema 的声明**
  （required/oneOf/别名属性/数值界/默认值）在生成时对真实 schema 做交叉断言，
  漂移即 `AssertionError`，无法靠改快照绕过。
- 冻结内容：5 工具集合；query 必填/长度 1..2000/top_k 1..50 默认 10/
  rerank 默认 true/no_rerank 别名；get_document(_chunks) 的 oneOf 与 doc_id
  别名；chunks 分页 page≥1、page_size 1..50 默认 20；输出别名
  n_results/citations/n_collections/doc_id/doc_type/source_path；
  get_document_summary 标记 compat_alias；v1 全量 schema 无 enum/const
  （新增枚举即契约事件，测试显式钉住空集合）。
- 错误文案已与源码逐字核对（含 `knowledge retrieval failed: <ExcType>`
  协议级错误面、not-found/forbidden 同形）。
- 漂移有效性实证（/tmp 一次性脚本，不入库）：新增工具、删除 required、
  删除 oneOf 分支、删除别名属性、引入 enum、篡改 fixture——六类均被
  非零退出/断言拦截；连续两次生成逐字节一致。
- 测试命令与结果：
  ```bash
  .venv/bin/python scripts/mcp_contract_inventory.py --check   # exit 0
  .venv/bin/python -m pytest tests/unit/test_mcp_contract_v1_snapshot.py -q
  # 11 passed in 0.43s
  .venv/bin/python -m pytest $(find tests -name '*mcp*.py') -q
  # 185 passed in 16.30s（17 个 MCP 相关测试文件，含既有 phase0 快照）
  git diff --check   # 无输出
  ```
- 既有失败分类：`tests/unit/test_metadata_enricher_contract.py` 2 项失败
  （prompt 文案断言）为本任务前既有失败，与本次新增文件无导入/修改关系，
  不在本任务修复。
- 遗留问题：无。

### 01.3 定义 Golden Set v1

- 状态：done
- 提交：`da6c2cb`（`test(retrieval): add versioned golden-set corpus`）
- 新增文件：
  - `tests/fixtures/retrieval_golden/corpus.json`（手工编写、全合成；
    7 文档 / 18 chunks；`synthetic: true`；零用户数据）
  - `tests/fixtures/retrieval_golden/schema.json`（2020-12 JSON Schema；
    schema_version 必填 const、id 模式、document/chunk id 模式、
    no_answer 条件式 if/then/else、filters 标量约束）
  - `tests/fixtures/retrieval_golden/cases.jsonl`（8 用例覆盖六类：
    exact_keyword×3 含中文/带页码、chinese_semantic、multicolumn_pdf、
    table、image_ocr、no_answer）
  - `tests/fixtures/retrieval_golden/README.md`（范围/非范围、ID 规则、
    重建方法、已知系统行为）
  - `scripts/eval_support.py`（seeder/evaluator 共用构造入口：
    DeterministicHashEmbedding、语料加载、生产 ID 公式、seed+manifest）
  - `scripts/seed_retrieval_fixtures.py`（CLI；默认数据目录被 gitignore；
    非空目录拒绝写入，--rebuild 覆盖；退出码 0/1/2）
  - `tests/unit/test_retrieval_golden_schema.py`（10 项离线校验）
  - `tests/integration/test_retrieval_golden_seed.py`（真实 Chroma+BM25；
    13 passed + 1 skip）
  - 修改：`.gitignore` 增加 `tests/fixtures/retrieval_golden/data/`
- 关键设计：
  - **不复制 ID 方案**：document id 用 `src.application.identifiers.document_uuid`
    （uuid5 公式），chunk id 用生产 `DocumentChunker._generate_chunk_id`
    （`{doc_id}_{index:04d}_{sha256(text)[:8]}`）；cases 中的冻结 ID 由
    测试用生产公式对语料文本重算校验，改字即失败。
  - 入库走真实生产组件：`ChromaStore`（临时/夹具数据目录）、
    `VectorUpserter`、`BM25Indexer`；数据目录布局与查询组合根一致
    （`db/chroma`、`db/bm25/golden_v1.json`）。
  - DeterministicHashEmbedding 对**生产 token 流**（SparseEncoder：
    ASCII run + CJK 1+2-gram + 同一停用词表）做 BLAKE2b 有符号 512 维
    哈希投影并 L2 归一化；无网络、不依赖 PYTHONHASHSEED；明确是
    词汇重叠几何而非神经语义，真实模型 profile 留给 01.5 显式命令。
  - 多栏 PDF/表格/OCR 类别诚实建模为**抽取后的文本层**
    （content_type=multicolumn_text/table_text/ocr_text，doc_type/pdf/image，
    page 元数据），不评测 OCR/PDF 解析模型本身（README 显式声明）。
- 设计前实证（probe 索引，未入库）：7 个可答用例在 dense/sparse/hybrid
  下目标均进 top-5（其中 7/7 在 sparse 与 hybrid 排名第 1；中文语义用例
  经一次问法/文本调整后三路第 1）；filters `doc_type=pdf` 生效；
  no-answer 在 sparse 严格返回空；dense/hybrid 返回零相似度行——
  生产 DenseRetriever 无分数阈值（README 与基线如实记录，本任务不修）。
- 确定性证据：两次重建 manifest 与 BM25 JSON 逐字节相同；
  manifest 无时间戳/主机名（内容哈希 + profile + 版本号的纯函数）。
- 测试命令与结果：
  ```bash
  .venv/bin/python scripts/seed_retrieval_fixtures.py   # 18 chunks/7 docs
  .venv/bin/python -m pytest tests/unit/test_retrieval_golden_schema.py \
      tests/integration/test_retrieval_golden_seed.py -q
  # 23 passed, 1 skipped
  .venv/bin/python -m pytest tests/unit/test_architecture_boundary.py \
      tests/unit/test_mcp_contract_v1_snapshot.py -q     # 26 passed
  git diff --check   # 无输出
  git check-ignore tests/fixtures/retrieval_golden/data/build_manifest.json  # 命中
  ```
- 遗留问题：无（dense 无阈值导致 no-answer FP 为**记录的既有行为**，
  在 01.5 基线中分模式量化）。

### 01.4 实现离线评测器

- 状态：done
- 提交：见本提交（`feat(eval): add deterministic retrieval evaluation runner`）
- 新增文件：
  - `scripts/eval_metrics.py`（纯指标层，无任何检索/存储代码）：
    `tie_normalized_rows`（`(score desc, chunk_id asc)`，None 排末尾，不改生产行为）、
    recall@K/precision@K/reciprocal_rank/DCG/nDCG（二元相关）、nearest-rank P50/P95、
    mean、`Aggregate.as_dict()`；`aggregate()` 严格只统计 `status=="ok"` 用例，
    infra error 永不并入，no-answer 只进 no-answer 计数器。
  - `scripts/run_retrieval_eval.py`（CLI 评测器）：经**应用层组合根**
    `scripts.query.build_query_components` + `QueryService`（与 MCP in-process
    client 同一构造路径），不走 Web/MCP 网络，不复制检索；Chroma 读
    `<data-dir>/db/chroma`，BM25 读 `<data-dir>/db/bm25/golden_v1.json`，
    embedding 仅 `DeterministicHashEmbedding`。
  - `tests/unit/test_eval_metrics.py`（14 项纯函数/聚合语义）
  - `tests/integration/test_run_retrieval_eval.py`（14 项，真实 Chroma+BM25）
- CLI 参数：`--data-dir`（默认 gitignore 的夹具数据目录）、`--cases`、
  `--mode dense|sparse|hybrid`、`--top-k 1..50`、`--rerank`、`--config`、
  `--profile ci|release`、`--output`（JSON）、`--report-md`。
- 指标与记录：Recall@K / Precision@K / MRR / nDCG@K，
  K = sorted({1, min(5,top_k), top_k})；document/chunk hit rate；
  no-answer accuracy + FP 单列；latency 用 `time.perf_counter()`（单调时钟）
  出 P50/P95/mean；平均返回字符数（按实际返回行）；branch contribution
  （`dense_count/sparse_count/fused_count` 汇总 + 每行 `RetrievalResult.source`
  计数 dense/sparse/fusion）。每案记录期望/实际 ID、relevant ranks、score、
  branch、`safe_preview`（压平换行、截断 120 字符），**不写完整正文**。
- rerank 语义镜像 `in_process.py`：`rerank.backend == none` 或构造异常 →
  `rerank.status=skipped`、run `status=degraded`、退出码仍 0，指标照常计算；
  缺失 reranker 绝不记零召回。运行前校验 manifest 存在且 embedding_profile
  匹配（不匹配 → 退出 1 提示重 seed）；sparse/hybrid 预检 BM25 文件。
- 退出码：0 完成（含 degraded）；1 基础设施失败（未 seed/缺 BM25/检索抛错，
  每案标 `status=error` 并截断错误信息 ≤300 字符，不与 no-answer 混淆）；
  2 参数/用例文件错误（不存在、非法 JSONL、schema 不合法、外集合）；
  3 评测整体 skip（如评估栈构造失败）。
- 实测结果（默认夹具，deterministic-hash-v1，top-10；latency 随机器波动不冻结）：
  - dense：R@1=0.571 / R@5=0.714 / R@10=1.0，MRR=0.654；no-answer 0/1
    （Chroma 无分数阈值 → FP，既有行为如实记录）。
  - sparse：R@1/R@5/R@10=1.0，MRR=1.0；no-answer 1/1（BM25 严格空）；
    7 案共返回 34 行（小语料不强行填满 top-k）。
  - hybrid：R@1/R@5/R@10=1.0，MRR=1.0；no-answer 0/1（FP 来自 dense 路）。
  - hybrid+rerank：rerank skipped（backend none）→ status=degraded、退出 0，
    指标与 hybrid 一致，不是零召回。
  - 同一固定索引连跑两次：每案 ID 排名与 score 完全一致。
  - 退出码实证：缺 cases=2、top-k 越界=2、坏 JSONL=2、外集合=2、
    未 seed=1、缺 BM25(sparse)=1；注入 `QueryService.search` 全量异常 →
    8/8 案 error、退出 1、`answerable_evaluated=0`、recall/precision/nDCG
    全为 None（不是 0）、no-answer 三计数全 0。
- 测试命令与结果：
  ```bash
  .venv/bin/python -m pytest tests/unit/test_eval_metrics.py -q
  # 14 passed in 0.04s
  .venv/bin/python -m pytest tests/integration/test_run_retrieval_eval.py -q
  # 14 passed in 0.79s
  .venv/bin/python -m pytest \
      tests/unit/test_architecture_boundary.py \
      tests/unit/test_mcp_contract_v1_snapshot.py \
      tests/unit/test_retrieval_golden_schema.py tests/unit/test_eval_metrics.py \
      tests/integration/test_retrieval_golden_seed.py \
      tests/integration/test_run_retrieval_eval.py -q
  # 77 passed, 1 skipped in 2.60s
  git diff --check   # 无输出
  ```
- 遗留问题：无。profile 阈值（ci/release 的 P95 上限）在 01.5 随基线快照落盘。

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

