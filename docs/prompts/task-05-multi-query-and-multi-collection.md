# Task 05 开发契约：多查询与多知识库检索

> 状态：已完成
> 日期：2026-09-16
> 前置：Task 04 Gate 已通过

## 1. 交付目标

在不引入查询生成或规划的前提下，把调用方显式给出的候选查询和多个已授权 collection 纳入同一 `search_chunks`。执行必须确定、可归因、权限 fail-closed，并保持 Task 04 单查询/单库行为兼容。

## 2. 冻结输入语义

- `query` 仍必填且永远排第一；`alternate_queries` 去首尾空白后按首次出现精确去重；
- alternate 默认最多 3 条，因此总查询数最多 4；每条服从 `query_max_length`，alternate 总字符服从 `alternate_query_total_max_chars`；
- `collection` 与 `collection_ids` 互斥；未指定时沿用单库选择规则；`collection_ids` 去重且保序；
- `failure_policy=fail_fast|allow_partial`，默认 `fail_fast`；授权失败和非法请求永不降级为 partial；
- 查询数 × collection 数 × 每路候选数受显式执行预算约束，越界返回 `invalid_request`。

## 3. 授权与失败边界

- HTTP principal 请求的每个 collection 必须都在 grant 中，任一越权则整单拒绝且不暴露具体集合是否存在；
- trusted-local principal 可显式选择多库，但不得通过空列表表达“全部库”；
- `fail_fast` 遇任一检索基础设施错误立即失败；`allow_partial` 仅对已授权 collection 的基础设施错误返回其余证据、failed collections 和稳定 Warning；
- filter 中的 `collection_ids` 只能继续缩小已选择集合，不能扩权。

## 4. 执行与融合

- 每个 `(collection, query)` 使用相同 mode/filter 独立召回；每库采用相同候选配额，避免大库吞噬候选；
- 先按 `(collection_id, document_id, chunk_id)` 去重，再用固定参数 RRF 融合；同分按稳定 ID 排序；
- 同一证据的 `matched_queries` 按规范化查询顺序记录且不重复；不同 collection 的同名 chunk 不得合并；
- rerank 只在全局去重融合后执行一次；threshold 作用于最终实际排序阶段；
- diagnostics 记录执行查询、成功/失败 collection 和候选计数，不泄露未授权资源。

## 5. 全链路变更

- 扩展 application `SearchRequest/SearchResult` 与统一搜索服务；
- Client 接口保持一个 `search` 方法，双实现及 `POST /internal/mcp/v1/search` 同步新字段；
- 扩展 MCP `search_chunks` Schema/输出；旧 `query_knowledge_hub` 继续固定单 query、单 collection；
- capabilities 启用 `multi_query`、`multi_collection_search`，公布实际预算和 failure policy；
- 更新冻结工具 Schema、capabilities 与只读 inventory。

## 6. 验收 Gate

覆盖规范化/预算、确定性 RRF、matched query 归因、一次 rerank、公平跨库候选、collection 参数冲突、整单拒绝、两种失败策略及单库兼容。双 Client、内部 API、stdio、Streamable HTTP 与 Golden Set 必须通过；任何指标变化必须显式记录。

## 7. 回滚

可关闭 capabilities 并把查询/collection 上限降为 1；解析层保留新增字段并返回明确不支持错误，避免静默忽略或破坏 Schema 客户端。

## 8. 实施记录（2026-09-16）

开发已按本契约完成。公开上限为 3 条 alternate、20 个 collection、聚合候选工作量 1000；capabilities 从真实 `ResponseBudget` 发布这些值。相关单元/契约测试分组 137 passed，双传输和 Golden Set Gate 46 passed、1 skipped（外部 embedding Provider 初始化不可用）。
