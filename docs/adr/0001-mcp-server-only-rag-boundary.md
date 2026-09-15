# ADR 0001：MCP Server-only 的 RAG 知识基础设施边界

- 状态：Accepted（2026-09-15）
- 决策者：SKDY-RAG 维护者
- 上下文任务：WeKnora MCP 能力路线任务 01（Phase 0–1）
- 相关文档：
  - `docs/plan-2026-09-15-weknora-mcp-capability-roadmap.md` §1
  - `docs/plans/weknora-mcp-roadmap/01-architecture-contract-and-retrieval-baseline.md`
  - `docs/prd-mcp-api-key-collection-access.md`
  - `docs/mcp-integration.md`

## 1. 背景

SKDY-RAG 通过 Model Context Protocol（MCP）向外部 Agent 暴露企业知识库的发现、
检索、证据展开与来源读取能力。随着功能增长，出现两类方向性诱惑：

1. 在服务端加入“帮 Agent 把问题想清楚”的能力——查询改写、多步规划、工具调用循环、
   最终答案生成；
2. 把管理面、对话面、Agent 运行时的能力也搬进 MCP Server。

这两类能力会模糊产品边界：一旦服务端承担问题理解与答案合成，调用方就被锁定在
特定答题行为上，权限、成本、评测和故障域也会失控。本 ADR 在功能扩张前把边界
冻结为可评审、可测试的硬约束。

## 2. 项目定位

SKDY-RAG 是**知识基础设施（Knowledge Infrastructure）**，不是 Agent Runtime，
也不是问答产品：

- 它向外部 Agent 提供**固定、单轮、无状态、无工具调用**的知识原语：发现知识库、
  搜索证据 Chunk、读取文档与 Chunk、展开上下文、核实原始来源；
- 它返回的是**带身份、分数、定位和权限边界的证据**，不是面向最终用户的答案；
- 它不保存调用方的会话、对话上下文或长期记忆。

一句话：**SKDY 负责“找得准、读得到、说得清出处”；外部 Agent 负责“问什么、
怎么拆、怎么用证据作答”。**

## 3. 决策

### 3.1 永久排除的 Agent 能力

以下能力在本项目中永久禁止进入产品代码（含 MCP 工具、Web/REST、应用服务、
摄取管道、脚本与后台任务）：

1. Agent Core、ReAct、Planner、Reflection 或任何 LLM/tool 调用循环；
2. 作为 MCP Client 连接、编排其他 MCP Server 或第三方 Agent 平台
   （**既有白名单例外**：`src/web_api/mcp_connection.py` 是管理面的一次性
   自检/健康探针——目标 URL 只来自服务端配置、单次 initialize → tools/list →
   一次 `list_collections`、硬超时、无循环、不编排第三方；它是诊断工具，不是
   Agent 能力，是本禁令唯一显式豁免点，新增 MCP Client 用法必须先修改本 ADR）；
3. Agent 会话、对话上下文、长期记忆、用户画像；
4. Skills、沙箱、Shell 执行、会话文件工作区、工具人工审批流；
5. 面向最终用户的答案生成、追问、推荐问题、对话摘要；
6. IM Bot、小程序、桌面端、聊天 Widget 等 Agent 发布渠道；
7. 自动 Wiki 式的自主知识产品形态；
8. 隐藏在检索管道内部的查询理解、隐式查询扩展或多步问题规划。

判定准则：**只要存在“根据模型的中间输出决定下一步调用什么”的控制流，即属于
Agent 能力，无论它是否自称 agent。** 固定分支、固定次数、结构化输出的管道不算。

### 3.2 允许的固定单轮模型阶段

摄取与检索管道允许存在**固定、单轮、无状态、无工具调用**的模型阶段，当前包括
但不限于：

- Rerank（cross-encoder 或单轮 LLM 打分）；
- 图片 OCR / Caption；
- Chunk Refine（固定规则的分块后整理）；
- 入库时的摘要、自动标签、合成问题等派生内容生成。

每个此类阶段必须同时满足：

1. **固定调用上限**：每个文档/查询的调用次数可静态确定，不随中间结果循环增长；
2. **结构化输出**：输出 schema 固定，可校验、可拒绝；
3. **显式超时与预算**：有超时、并发与 token/费用预算；
4. **开关**：可通过配置整体关闭，关闭后管道仍可运行（可能降级）；
5. **失败可观测且可降级**：失败产生显式 degraded 信号并回退到无该阶段的路径，
   绝不伪装成正常完整结果，也不能根据失败内容再发起新的模型调用；
6. **无工具调用**：模型不能调用任何工具或触发副作用。

固定单轮模型调用不等于 Agent；但**不得根据其中间结果继续规划或循环调用**。

### 3.3 职责边界

| 层 | 职责 | 不做什么 |
| --- | --- | --- |
| **MCP Server（Agent 面）** | 认证（API Key/Bearer）、输入校验、collection 授权、DTO/Resource 映射；暴露少量只读知识原语 | 不实现检索逻辑、不直接构造 Embedding/VectorStore/LLM、不生成答案、不持有会话 |
| **管理 REST API / Web（管理面）** | 上传、摄取、任务、配置、Trace、知识库治理等人类管理员能力 | 不必全部 MCP 化；不向外部 Agent 开放写能力 |
| **内部应用服务（Application Services）** | 检索编排（dense/sparse/hybrid/rerank）、摄取编排、文档/任务/Trace 领域逻辑；MCP 与 REST 共用同一应用层 | 不依赖 MCP 协议类型；不包含工具循环或答案合成 |
| **库与基础设施（libs/ingestion/core）** | Embedding、向量库、BM25、解析、分块、固定单轮模型阶段 | 不感知调用方身份之外的会话状态 |
| **外部 Agent（项目外）** | 问题理解、查询扩展、子问题拆解、证据编排、最终答案、与用户对话 | 不属于本仓库 |

其他约束：

- **MCP 默认只读**。当前全部 MCP 工具均为只读；未来如确需写工具，必须独立立项、
  独立权限面、幂等、可审计，不能复用只读 Key。
- **大型正文与图片优先通过 MCP Resource 按需读取**（见后续 Phase 8）；工具只返回
  发现结果与有界的小型结构化证据，不在发现类工具中内联无界正文。
- **管理能力不要求全部 MCP 化**。REST/Web 与 MCP 是两个产品面；只有外部 Agent
  确实需要的只读原语才进入 MCP。
- **应用层不依赖 MCP**：MCP 层只做认证、校验、授权和 DTO/Resource 映射。
- **外部 Agent 负责问题理解与查询扩展**；服务端只接受 Agent 显式提供的原始查询与
  可选 `alternate_queries`（Phase 6），自身不生成、不隐式改写查询。
- **SKDY 不生成最终答案**。工具描述与返回结构不得引导调用方把服务端文本当作
  最终答案；`query_knowledge_hub` 明确返回 evidence 而非 answer。

### 3.4 安全与权限原则

- 所有授权在服务端执行，输入参数只能**缩小**已授权集合，不能扩大：
  - 详见 `docs/prd-mcp-api-key-collection-access.md`；
  - HTTP 缺少认证 principal 时 fail-closed；stdio 使用显式的
    `TrustedLocalPrincipal`，不存在“principal 为 None 就放行”。
- 不存在与无权限资源对外同形（不泄露资源是否存在）。
- 工具不回传本地存储路径、密钥、堆栈；审计日志不记录正文与凭证。
- 边界保护测试只能使用**精确路径/符号白名单**，不得因普通词汇
  （第三方依赖名、文档措辞、历史命名）误报。

### 3.5 现状盘点（2026-09-15 基于代码的审计结论）

本 ADR 立档时对生产代码的审计结果（后续如变化必须在任务状态文档记录）：

| 阶段 / 能力 | 现状入口 | 属性 |
| --- | --- | --- |
| Rerank | `src/core/query_engine/reranker.py::RerankerStage`；`src/libs/reranker/`（`none`/`cross_encoder`/`llm`） | 固定单轮：每查询一次调用，`top_m` 上限；后端异常整体回退原序（`fallback=True`），不重试不循环 |
| Chunk Refine（C5） | `src/ingestion/transform/chunk_refiner.py` | 每块一次 LLM；`use_llm` 默认关；`fallback_on_error` 回退规则结果 |
| 摘要 / 自动标签（C6） | `src/ingestion/transform/metadata_enricher.py` | 摄取时一次 LLM；`use_llm` 默认关；长度/数量上限；失败回退 |
| 图片 Caption（C7） | `src/ingestion/transform/image_captioner.py` | 每图一次 VLM；`use_llm` 默认关；长度上限；失败回退 |
| OCR / 文档解析 | `src/document_parser/`（docreader 外部解析服务，整文档单次解析） | 固定基础设施调用，无工具循环 |
| 查询处理 | `src/core/query_engine/query_processor.py` | **纯规则**分词（与索引共用 `SparseEncoder`），无 LLM、无查询改写；`ProcessedQuery.expanded` 无业务写入点 |
| 查询工具输出 | `src/mcp_server/tools/query_knowledge_hub.py` | 只返回 evidence，无 answer 字段（测试强制断言） |
| MCP Client 用法 | 仅 `src/web_api/mcp_connection.py`（管理面自检探针，见 §3.1 豁免） | `src/mcp_server/` 内无任何 `mcp.client` 导入 |
| Agent/会话/记忆 | 未发现 Agent Core、ReAct、Planner、Reflection、会话存储、长期记忆代码 | — |

## 4. 正例与反例

### 4.1 正例（允许）

- 查询进入后，固定执行 dense + sparse → RRF 融合 → （可选）**一次** rerank，
  返回带分数与出处的 evidence。
- 摄取文档时，在固定阶段调用一次 VLM 生成图片 caption 写入元数据；VLM 不可用时
  记录 degraded 并保留无 caption 的索引。
- Agent 在自己的系统里把问题拆成 3 个子问题，分别调用 MCP 工具，再由 Agent
  自己合成答案。
- Agent 显式传入 2 条 `alternate_queries`（未来 Phase 6），服务端分别检索后
  固定融合、去重、返回，并标注每条证据命中了哪条查询。
- 管理 Web 页面触发摄取、查看 Trace——这些留在 REST/Web，不包装成 MCP 工具。

### 4.2 反例（禁止）

- “先检索看结果，结果不够就让 LLM 改写查询再检索，直到够为止”——工具循环。
- 服务端提供 `chat`/`ask`/`answer` 工具，把检索结果喂给 LLM 生成最终回答。
- MCP Server 作为 MCP Client 去连其他 MCP Server 做“多智能体编排”。
- 保存 Agent 的多轮对话历史，下次调用自动带上做上下文推理。
- rerank 失败后用 LLM 判断“要不要换一种方式重试”并再次调用模型。
- 在查询工具内部静默做 LLM 查询扩展，且不向调用方暴露发生了扩展。
- 新增 `src/agent/`、`src/planner/` 等模块实现上述任一能力。

## 5. 对后续开发与评审的约束

1. 每个 PRD/计划/代码评审涉及新能力时，必须引用本 ADR，并回答：
   - 它是否新增了工具调用循环或多轮模型控制流？
   - 它是否让服务端产生了面向最终用户的答案或会话状态？
   - 它是否在 MCP 上增加了写能力或扩大了授权？
   - 模型阶段是否满足固定上限、结构化输出、超时预算、开关、降级五要素？
2. 检索/索引行为变更必须通过任务 01 建立的 Golden Set 基线回归。
3. MCP 契约变更（工具增删、必填字段、枚举、oneOf、兼容别名）会被契约快照测试
   阻断，必须显式更新快照并说明兼容性。
4. 新增模型阶段默认关闭（opt-in），且必须有降级测试；不得为“修复”评测回归而
   在基线任务中调整产品阈值。
5. 本 ADR 的机器保护见 `tests/unit/test_architecture_boundary.py`：
   - 禁止在 `src/` 下新增名为 `agent`/`agents`/`planner`/`react`/`reflection`/
     `conversation`/`conversations`/`memory`/`skills`/`sandbox` 的顶层包或模块；
   - 禁止在产品代码中新增 MCP **Client** 侧依赖（`mcp.client` 导入）；
     唯一白名单是管理面自检探针 `src/web_api/mcp_connection.py`
     （见 §3.1 第 2 条豁免）；MCP Server 自身（`src/mcp_server/`）永远只
     导入 `mcp.server`/`mcp.types` 等服务端符号；
   - 检查采用精确路径与显式白名单，不扫描第三方依赖、`.github/`、`docs/`、
     `tests/` 中的普通词汇。

## 6. 决策后果

正面：

- 产品边界清晰，外部 Agent 的自治空间不被侵蚀；
- 权限、成本、延迟和评测的故障域收敛在“无状态单轮管道”内；
- 检索质量可以通过离线 Golden Set 做确定性回归，不被生成式行为干扰；
- MCP 工具面小而稳定，客户端兼容窗口可管理。

负面 / 代价：

- 查询扩展、多步拆解、答题体验必须由外部 Agent 实现，开箱即用的“聊天”体验
  不在本项目内；
- 某些看似方便的功能（服务端 `expand_query`、按算法拆分多只搜索工具、自动
  Wiki）被明确拒绝或推迟；
- 摄取侧固定单轮模型阶段需要各自实现开关、预算与降级，工程成本高于“直接调一下
  LLM”。

## 7. 复审条件

本 ADR 可在以下任一条件被**显式**触发时复审（复审不等于自动推翻）：

1. 出现真实、反复的外部消费者需求，证明只读证据原语无法支撑其场景，且替代方案
   （由 Agent 侧实现）被评估为不可行；
2. MCP 规范本身提供了合适的有状态/资源契约，使新能力可以在不引入工具循环和
   会话记忆的前提下落地；
3. 组织决定把本项目产品定位从“知识基础设施”变更为“Agent Runtime / 问答产品”
   ——该决定必须先于代码发生，并更新本 ADR 或用新 ADR 取代。

复审必须评估：权限模型、成本与速率边界、Golden Set 评测是否仍成立、客户端兼容、
以及是否需要独立部署形态（默认仍保持只读 server 可用）。
