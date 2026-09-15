# WeKnora 能力借鉴：MCP 知识基础设施线性开发路线

> 日期：2026-09-15  
> 状态：待实施  
> 参考：`/home/hello/workspace/WeKnora/docs/WeKnora项目功能模块与架构总结.md`  
> 前置成果：DocReader 迁移、只读 MCP 优化、知识管理与 Trace UX 已完成的相关计划及实施记录

## 1. 产品定位与不可违背的边界

SKDY-RAG 是通过 MCP 向外部 Agent 提供知识发现、精确检索、证据展开和来源读取能力的知识基础设施，
不是 Agent Runtime，也不是最终答案生成系统。

### 1.1 永久排除

- 不实现 Agent Core、ReAct、Planner、Reflection 或任何工具调用循环；
- 不作为 MCP Client 连接或编排其他 MCP Server；
- 不保存 Agent 会话、对话上下文、长期记忆或用户画像；
- 不实现 Skills、沙箱 Shell、会话文件工作区和工具人工审批；
- 不生成面向最终用户的答案、追问、推荐问题或对话摘要；
- 不建设 IM Bot、小程序、桌面端、聊天 Widget 等 Agent 发布渠道；
- 不照搬 WeKnora 自动 Wiki 产品形态；
- 不把查询理解和多步决策隐藏在检索管道内部。

### 1.2 允许的模型调用

摄取或检索管道可以包含单轮、固定、无状态、无工具调用的模型阶段，例如：

- Rerank；
- 图片 OCR/Caption；
- Chunk Refine；
- 入库时的摘要、自动标签和合成问题。

每个阶段必须满足：固定最大调用次数、结构化输出、显式超时和预算、可配置关闭、失败可观测且可降级。
任何根据中间结果继续规划或调用工具的行为均属于 Agent 侧能力，禁止进入本项目。

### 1.3 对外能力原则

- 外部 Agent 负责问题理解、查询扩展、子问题拆解和证据使用；
- SKDY 接受 Agent 提供的原始查询和可选 `alternate_queries`，只负责检索、融合与溯源；
- 管理 REST API/Web 与 Agent MCP 接口是不同产品面；不是所有内部能力都需要包装为 MCP 工具；
- MCP 默认保持只读。写工具若未来确有需求，必须使用独立权限面、幂等和审计，另行立项；
- 工具负责发现和小型结构化结果，大型正文、图片等优先通过 MCP Resource 按需读取。

## 2. 实施原则

严格按 Phase 0 → Phase 12 线性推进。后续 Phase 不得在前一 Phase Gate 未通过时开始。

每个任务统一满足：

1. 先冻结输入、输出、错误和权限契约；
2. 应用层实现不依赖 MCP 协议，MCP 层只负责认证、校验和 DTO 映射；
3. 所有过滤条件只能缩小 API Key 已授权范围；
4. 覆盖正常、空结果、越权、超时、上游失败和旧数据兼容测试；
5. 同步 OpenAPI、MCP Schema、生成类型和契约快照；
6. 检索或索引变更必须附 Golden Set 前后对比；
7. 记录配置、限制、部署要求和回滚方式；
8. 每个提交只完成一个可独立验证的小任务。

## 3. 线性依赖总览

```text
架构约束与现状契约
  → 检索评测基线
  → MCP 公共数据契约
  → 基础读取原语
  → 统一搜索原语
  → 元数据过滤
  → 多查询与多知识库
  → 父子分块
  → 来源资源读取
  → Chunk 版本治理
  → 摄取自动增强
  → 任务治理与审计
  → 外部数据源同步
```

## 4. Phase 0：冻结架构和现有 MCP 契约

### T0.1 架构决策记录

- 将第 1 节写入正式 ADR；
- 给出允许/禁止的模型调用示例；
- 明确 MCP Server-only、无会话、无答案生成和默认只读；
- 后续相关 PRD、计划和代码评审必须引用该 ADR。

### T0.2 盘点并冻结现有 MCP 契约

- 记录当前工具名称、输入 Schema、输出结构、错误、别名和权限语义；
- 标记长期保留、兼容入口和未来废弃项；
- 建立版本化 Schema 快照和 CI 漂移检查；
- 保证现有客户端调用不变。

### Phase 0 Gate

- ADR 审核通过；
- 当前 MCP 全部契约测试通过；
- 本阶段不改变运行行为。

## 5. Phase 1：建立纯检索评测基线

### T1.1 Golden Set 契约与样本

每条样本包含 query、collection、相关 document/chunk、可接受来源位置、过滤条件和场景标签。
首批覆盖精确关键词、语义改写、中文、表格、图片 OCR、多栏 PDF、跨页和无答案查询。

### T1.2 离线评测运行器

输出 Recall@K、Precision@K、MRR、nDCG、文档/Chunk 命中率、无结果准确率、P50/P95 延迟、
返回字符数和各召回分支贡献。失败报告必须能定位具体查询、候选和排序阶段。

### T1.3 当前实现快照

分别记录 Dense、BM25、Hybrid、Hybrid+Rerank，并验证 Rerank 不可用时的降级行为。

### T1.4 CI 回归门禁

设定关键集合召回、无答案误召回、延迟和契约完整性的初始阈值。后续根据稳定数据逐步收紧。

### Phase 1 Gate

- 固定环境可重复运行；
- 相同配置结果稳定；
- 已提交不含敏感正文的基线快照；
- 后续检索改动可自动生成前后对比。

## 6. Phase 2：统一 MCP 公共契约

### T2.1 Evidence 契约

统一定义 collection/document/chunk ID 与版本、parent ID、标题、正文或预览、内容类型、标题路径、
来源定位、资源 ID、各阶段分数、命中查询和索引时间。未执行的评分字段返回 null，不伪造；不得泄露本地路径。

### T2.2 Filter 契约

统一支持 collection、document、tag、folder、是否递归子目录、文件类型、内容类型、更新时间和来源类型。
明确多标签 AND/OR、空数组、缺省字段、枚举、日期及数组上限语义。

### T2.3 分页、预算、Warning 和错误

- 明确各工具采用游标还是页码分页；
- 统一 Top-K、Evidence 数、正文字符数和估算 Token 上限；
- 定义截断、Rerank 降级、旧数据缺字段、部分 collection 失败等 Warning；
- 区分无结果、无权限、无此资源、超时、过载和上游不可用。

### T2.4 Server capabilities

通过 MCP Resource，或在客户端兼容性不足时通过 `get_server_capabilities`，公开契约版本、检索模式、
过滤器、上限、内容类型、父块/资源/多 collection 支持情况和写工具开关。

### Phase 2 Gate

- Evidence、Filter、Error、Warning 契约冻结；
- OpenAPI、Python 类型、前端类型、MCP Schema 一致；
- 旧工具兼容测试通过；
- 公共契约不依赖具体向量库。

## 7. Phase 3：基础读取原语

### T3.1 `list_documents`

复用现有管理 API 的筛选能力，返回文档 ID、标题、类型、标签、文件夹、版本、更新时间、处理状态、
Chunk 数和可用内容类型，不返回无界正文。

### T3.2 `get_chunk`

按 document ID + chunk ID 读取完整正文、Evidence 元数据、前后 Chunk、父 Chunk、来源定位和资源 ID。
必须验证 Chunk 的文档归属与 collection 授权。

### T3.3 整理 `get_document`

补齐文档版本、标签、文件夹、来源类型、解析器、索引状态、资源类型、Chunk 数和更新时间；
避免内联无限量 Chunk。

### T3.4 兼容层

保留 `get_document_summary`、`doc_id` 等既有兼容面，所有新旧输入进入 Schema 快照。

### Phase 3 Gate

- 外部 Agent 可完成知识库发现 → 文档发现 → 文档读取 → Chunk 精确读取；
- 跨 collection、错文档 Chunk 和路径泄漏测试通过；
- 大文档响应有界。

## 8. Phase 4：统一搜索原语

### T4.1 应用层 SearchRequest

定义 queries、retrieval mode、filters、top_k、rerank、score threshold、是否返回正文和响应预算，
避免 MCP 与 REST 各自实现检索逻辑。

### T4.2 `search_chunks`

用一只工具支持 `dense|sparse|hybrid`，不按算法膨胀工具数量。返回统一 Evidence，保留 dense、sparse、
fusion、rerank 分数；相同 Chunk 去重；Rerank 失败通过 Warning 表达，不能伪装为空结果。

### T4.3 旧查询入口委托

保留 `query_knowledge_hub` 作为兼容入口，内部统一委托 `search_chunks(mode=hybrid)`，删除重复实现。

### T4.4 管理侧诊断模式

通过管理 API 或离线 CLI 展示各召回分支、融合排名、Rerank 前后变化、过滤和截断原因；
MCP 默认只返回 Agent 决策需要的分数与 Warning。

### Phase 4 Gate

- 三种模式通过 Golden Set；
- 旧工具无不可接受回归；
- 无结果和基础设施失败严格区分；
- MCP 对外只有一只通用搜索工具。

## 9. Phase 5：治理元数据进入检索

### T5.1 存储层过滤

接入标签、文件夹/子目录、格式、日期、来源和内容类型过滤，优先在存储查询阶段执行，避免无界召回后过滤。

### T5.2 权限交集

每次搜索计算 API Key 授权集合、请求 collection、文档过滤结果与 Chunk 实际归属的交集，并在边界再次复检。

### T5.3 过滤评测

增加同名文档、跨目录同关键词、标签 AND/OR、递归目录、无权限资源和过滤后无结果用例。

### Phase 5 Gate

- Web/REST 与 MCP 过滤语义一致；
- 过滤不能扩大权限；
- 组合过滤保持稳定排序；
- Trace/审计只记录安全的过滤摘要。

## 10. Phase 6：多查询与多 Collection

### T6.1 `alternate_queries`

允许外部 Agent 提供有数量和长度上限的替代查询。SKDY 不生成查询，只分别检索、融合去重，
并在 Evidence 的 `matched_queries` 中记录命中来源。

### T6.2 多 Collection 授权

请求 collection 必须是 API Key 授权集合的子集。默认任一目标未授权即整体拒绝，不静默删除未授权目标。

### T6.3 每库配额与跨库融合

每库独立召回并设置候选配额，使用 rank-based fusion 处理不可直接比较的分数，始终保留 collection 身份。

### T6.4 部分基础设施失败

支持配置整体失败或部分成功；部分成功必须显式返回 failed collections 和 Warning，不能解释为没有相关知识。

### Phase 6 Gate

- 多查询融合有评测收益；
- 大库不会完全挤占小库；
- 未授权目标 fail-closed；
- 单 collection 行为兼容。

## 11. Phase 7：父子分块与上下文读取

### T7.1 父子数据模型

定义稳定 chunk/parent ID、层级、顺序、标题路径、source span、document/chunk version。

### T7.2 父块生成

Markdown/DOCX 按章节、PDF 按标题区间或页组、PPTX 按页、表格按完整表或逻辑分区、无结构文本按大递归块生成。

### T7.3 子块索引

子块用于主要召回，父块是否索引由配置决定；子块继承父块的定位和治理元数据，图片子块关联所属父块。

### T7.4 `get_chunk_context`

支持 parent、neighbors、parent-and-neighbors 模式及 before/after/max_chars 预算；返回关系类型和截断信息，
引用仍精确指向命中子块。

### T7.5 旧数据迁移

旧文档继续可读且 parent ID 为 null；后台逐文档重建；失败保留旧索引；完成后原子切换索引版本。

### T7.6 对比评测

比较仅子块、父块展开、邻接窗口、不同父块大小及响应成本。

### Phase 7 Gate

- 旧数据兼容；
- 查询不读取半套索引；
- Golden Set 证明收益；
- 父块展开不损失精确引用。

## 12. Phase 8：来源资源读取

### T8.1 Asset 契约

覆盖 PDF 页面图、扫描页、内嵌图片和表格快照，定义 asset/document/chunk ID、MIME、大小、定位和 checksum。

### T8.2 MCP Resource

建议使用 `rag://documents/{document_id}/chunks/{chunk_id}` 和
`rag://documents/{document_id}/assets/{asset_id}`。搜索工具返回 Resource Link，由 Agent 按需读取。

### T8.3 资源安全

每次读取复检授权，限制 MIME 和大小，不接受任意路径，不回传存储路径，支持 checksum/ETag，
审计资源访问但不记录正文。

### Phase 8 Gate

- 外部 Agent 可读取 PDF 页面、扫描页和内嵌图片；
- 路径遍历与跨库访问测试通过；
- 搜索响应不会因图片而失控。

## 13. Phase 9：Chunk 编辑、版本、Diff 与回滚

### T9.1 不可变版本模型

定义 Document/Chunk revision、当前生效版本、编辑来源、修改原因、操作者和时间。

### T9.2 管理 API 编辑

第一版只通过 REST/Web 开放，使用乐观锁或 `If-Match`，限制可编辑字段；冲突返回 409，保存后进入待索引状态。

### T9.3 Diff 与回滚

提供版本列表、版本详情和行级 Diff；回滚产生新版本而非覆盖历史，并触发相关索引重建。

### T9.4 局部索引重建

协调 Dense、BM25、向量库、父子关系和派生索引，使用版本切换避免新旧混合。

### T9.5 MCP 陈旧引用

明确旧版本 ID 是允许读取历史，还是返回 `stale_reference` 并指向当前版本；禁止静默返回不同内容。

### Phase 9 Gate

- 编辑、失败、重试和回滚不破坏当前可查询版本；
- MCP 能识别版本与陈旧引用；
- 所有修改进入审计记录。

## 14. Phase 10：摄取自动增强

### T10.1 自动标签

优先匹配已有标签；新标签仅作为候选；人工与模型标签分开；保存模型、Prompt 版本和置信度。

### T10.2 摘要

固定单轮、长度受限、标记为派生内容，不能替代原文 Evidence。

### T10.3 合成问题

与原文索引隔离，记录 `derived_from_chunk_id`，可单独加权、关闭、删除和重建；引用始终回到原文 Chunk。

### T10.4 成本与收益

记录调用次数、Token、成功率、延迟和每文档成本，并使用 Golden Set 验证检索净收益。

### Phase 10 Gate

只有检索指标稳定提升、误召回可接受、成本在预算内且可逆的增强能力才允许默认启用。

## 15. Phase 11：任务治理、限流与审计

### T11.1 统一任务阶段

固定 parse → normalize → chunk → enrich → embed → index → finalize，记录状态、attempt、计数、时间、
错误码、retryable 和 cancelable。

### T11.2 Worker Pool 抽象

按 Parse、Enrich、Index、Maintenance、Sync 分池。先抽象接口并保留 Lite 本地实现，不强制引入 Redis。

### T11.3 重试与死信

实现指数退避、最大次数、错误分类、死信、管理员重放及同文档版本幂等。

### T11.4 资源限流

分别治理 DocReader、Embedding、Rerank、VLM、RPM/TPM、MCP Key 调用频率和返回预算。

### T11.5 审计

记录 MCP 工具与 collection、文档/Chunk 管理、Key 生命周期、版本回滚、同步、批量任务和权限拒绝；
不默认记录完整查询、Evidence 正文、Secret 或本地路径。

### Phase 11 Gate

- Worker 重启后任务可恢复；
- 死信可发现和重放；
- 取消不留下不可识别半成品；
- 过载使用稳定错误码；
- 审计不泄露敏感数据。

## 16. Phase 12：外部数据源同步

本阶段依赖版本、任务、审计和安全基础完成后再开始。

### T12.1 Connector 接口

定义列举远端文档、获取内容、revision、checkpoint、删除检测、连接测试和关闭客户端。

### T12.2 数据源模型

保存类型、配置、加密凭据、collection 绑定、同步模式、checkpoint、最后状态和下次执行时间。

### T12.3 SSRF 与凭据安全

实现协议、域名、IP/CIDR 白名单，DNS 检查、重定向逐跳验证、metadata endpoint 拦截、凭据加密和日志脱敏。

### T12.4 首个轻量连接器

优先 RSS 或受控 URL Sitemap，支持首次全量、ETag/Last-Modified 增量、内容哈希、远端删除、同步日志和重试。

### T12.5 人工编辑冲突

远端更新产生新 revision，不静默覆盖人工版本；冲突进入待处理状态，由管理员选择重放人工修改或接受远端版本。

### T12.6 MCP 只读状态

可开放 `list_data_sources`、`get_sync_status`、`list_sync_failures`；暂不开放触发同步等写工具。

### Phase 12 Gate

- 增量、重复同步和远端删除行为可预测；
- 人工修改不会静默丢失；
- SSRF 与凭据测试通过；
- 同步失败不破坏当前可查询版本。

## 17. 交付里程碑

### M1：MCP 检索原语可用（Phase 0–5）

完成架构边界、评测基线、统一契约、文档/Chunk 读取、统一搜索和元数据过滤。

### M2：复杂证据读取（Phase 6–8）

完成多查询、多 collection、父子分块、上下文展开和来源资源读取，使外部 Agent 可以执行：

```text
发现 → 搜索 → 缩小范围 → 展开上下文 → 核实原始资源
```

### M3：知识资产持续维护（Phase 9–12）

完成版本治理、自动增强、任务/审计和外部同步，使知识资产可纠错、可追溯、可持续更新。

## 18. 当前明确不立项

- 独立 `expand_query` 工具：优先由外部 Agent 生成 `alternate_queries`；
- 按算法拆成多只搜索工具：统一收敛到 `search_chunks.retrieval_mode`；
- GraphRAG：不是架构上禁止，但在缺少多跳关系检索需求和评测证据前不进入路线图；
- 自动 Wiki：不符合本项目产品边界；
- MCP 写工具：当前版本不开放，未来如有明确自动化需求需独立安全评审；
- 大规模模型、向量库和对象存储 Provider 矩阵：只建设可替换接口，不追求供应商数量。

## 19. 成功标准

本路线完成后，SKDY-RAG 应具备以下稳定能力：

1. 外部 Agent 可以通过少量、稳定的 MCP 原语发现知识、搜索 Chunk、读取上下文和核实来源；
2. 每条 Evidence 具有稳定身份、版本、权限边界和原文定位；
3. 检索质量、延迟、响应预算和降级行为可以被持续评测；
4. 知识可编辑、可回滚、可增量重建且不会出现半套索引；
5. 摄取、增强、索引和同步任务可重试、可取消、可审计；
6. 系统始终不承担 Agent 的规划、循环、会话和最终答案职责。
