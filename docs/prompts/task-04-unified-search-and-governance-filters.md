# Task 04 开发契约：统一检索与治理过滤

> 状态：已完成
> 日期：2026-09-16
> 前置：Task 03 Gate 已通过

## 1. 交付目标

新增唯一长期检索原语 `search_chunks`，统一 `dense`、`sparse`、`hybrid` 三种模式，并把 `EvidenceFilterV1` 从声明契约落实为 collection-scoped 治理过滤。旧 `query_knowledge_hub` 必须委托同一应用服务，仅保留兼容输入输出。

## 2. 现状审计

- `QueryService.search()` 已支持三模式，但仍返回旧 `QueryResult/ RetrievalResult`，filter 仅直接传给 dense/hybrid，sparse 依赖融合后 metadata post-filter。
- `InProcessRagReadOnlyClient.query_knowledge()` 自行构建检索与 rerank，并直接映射旧 `KnowledgeQueryResult`；这是需要收敛的第二套编排。
- `EvidenceV1`、`EvidenceScores`、`EvidenceFilterV1`、Warning/Error vocabulary 已冻结，但尚无统一 `SearchRequest/SearchResult` 消费它们。
- WebApiDB 已保存 tag、folder、document placement；DocumentService 已能将稳定 document UUID 解析回 collection/source path。
- Task 03 已打通双 Client、版本化内部 API、scope header 与 7 工具 capabilities。

## 3. 应用层契约

新增 transport-neutral：

- `SearchRequest(query, collection, mode='hybrid', filters, top_k, rerank, threshold, include_content, response_budget)`；
- `SearchResult(query, collection, mode, evidence, warnings, diagnostics, candidates, truncated)`；
- mode 仅允许 `dense|sparse|hybrid`；query、top_k、filter 数量共用 `ResponseBudget`；
- 应用层不得 import MCP/FastAPI/Chroma DTO。

Score 语义：dense/sparse 记录各分支原始分数；hybrid 的最终排序分数写 fusion；rerank 执行时写 rerank。未执行阶段必须为 null。threshold 作用于最终实际排序阶段（rerank > fusion > 单路 score），之后再 top-k。

稳定性：以 `(collection_id, document_id, chunk_id)` 去重和同分 tie-break；同一 chunk 多路命中合并 score，不复制 Evidence。

## 4. 治理过滤

过滤首先解析成当前 collection 内允许的 document/source 集合，再与 API Key collection scope 取交集：

- document IDs：必须归属当前 collection；越权/未知 ID 不泄露存在性；
- tag：AND 为全部匹配，OR 为任一匹配；
- folder：`root` 表示根；`include_descendants` 使用受限子树；
- file/content/source type、updated range 在候选进入最终排序前执行；
- 空过滤结果是成功空结果；存储/索引异常不得伪装为空结果；
- 单次最多 20 个 document/tag/type 值，禁止任意 metadata 表达式。

## 5. 接口与兼容

- Client 新增 `search(SearchRequest, principal)`；HTTP 使用 `POST /internal/mcp/v1/search`；
- MCP 新增 `search_chunks`，Schema 是 SearchRequest 的安全子集；输出为 Evidence、warnings、diagnostics 与预算信息；
- `query_knowledge_hub` 只构造 `mode=hybrid` 的 SearchRequest 并调用同一方法；保留 `no_rerank`、`n_results`、`citations`；
- 不保留第二套查询、rerank 或 Evidence 映射实现；
- capabilities 注册 `search_chunks=true`、`governance_filters=true`，过滤 enforced 状态改为 true。

## 6. 实施批次

1. 新增 Search 契约、验证、稳定排序/阈值测试。
2. 在应用服务统一三模式与 Evidence 映射。
3. 新增治理过滤解析器并接入存储前置约束和最终防御过滤。
4. 扩展 in-process/HTTP Client 与内部 API。
5. 注册 `search_chunks`，将旧工具改为适配器。
6. 更新 capabilities、冻结快照、集成文档和诊断输出。
7. 运行单元、双传输、权限、性能及 Task 01 Golden Set Gate。

## 7. 验收与回滚

验收覆盖三模式、空结果、阈值、去重、同分排序、组合过滤、越权探测、rerank 降级、Embedding/BM25 故障、旧工具等价及 Golden Set 无未批准回归。

回滚可注销 `search_chunks` 并让兼容工具临时切回旧适配入口；统一 Search 契约和治理过滤服务保留。任何双实现回退必须显式记录，不得长期静默存在。

## 8. 实施记录（2026-09-16）

- 已新增 transport-neutral `SearchRequest/SearchResult`、稳定去重/排序与最终阶段 threshold；
- 已实现三模式统一服务及 collection-scoped 文档、标签、目录递归、文件/内容/来源类型、更新时间过滤；
- 已贯通 in-process/HTTP Client、`POST /internal/mcp/v1/search` 和 MCP `search_chunks`；
- `query_knowledge_hub` 已委托统一搜索服务，capabilities 与三份冻结契约快照已更新为 8 工具；
- 验证结果：Task 03/04 相关单元与契约测试 97 项通过；双传输、capabilities 与 Golden Set 集成 Gate 45 项通过；契约清单检查和 `compileall` 通过。
