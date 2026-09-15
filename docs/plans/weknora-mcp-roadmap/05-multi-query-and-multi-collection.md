# 任务 05：多查询与多知识库检索

> 状态：待实施；前置：任务 04 Gate；后继：任务 06

## 目标与边界

允许外部 Agent 显式提交多个查询并跨多个已授权 collection 检索。SKDY 仅执行、融合和溯源，不生成替代查询，
不做查询规划或父子展开。扩展 `search_chunks`：必填 `query`，可选 `alternate_queries`（默认最多 4 条）、
`collection_ids`、`failure_policy=fail_fast|allow_partial`；Evidence 增加 `matched_queries`。

## 原子任务

1. **05.1 多查询规范化**：去空白、精确去重，原 query 固定第一位；限制单条/总字符和查询数×召回数预算。
   提交：`feat(search): accept bounded agent-supplied alternate queries`。
2. **05.2 多查询融合**：逐 query 执行相同 mode/filter，以稳定 Chunk ID 去重，记录命中 query；使用固定参数
   RRF；Rerank 仅对最终去重候选执行一次。提交：`feat(search): fuse multi-query evidence deterministically`。
3. **05.3 多库授权**：请求集合必须是 API Key 授权集合的子集；任一未授权整体拒绝；新旧 collection 参数
   冲突时报 invalid_request。提交：`feat(auth): validate multi-collection search scope`。
4. **05.4 公平跨库融合**：每库独立召回并设候选配额；使用 rank-based fusion；保留 collection ID，禁止跨库
   错误去重。提交：`feat(search): add fair cross-collection retrieval fusion`。
5. **05.5 部分失败**：fail_fast 任一基础设施失败即报错；allow_partial 返回成功证据、failed collections 和
   Warning；权限失败永不 partial。提交：`feat(search): expose explicit partial collection failures`。
6. **05.6 全链路**：同步 in-process、HTTP、MCP Schema、capabilities；扩展 Golden Set 比较收益和延迟。
   提交：`test(search): verify multi-query and multi-collection behavior`。

## 测试与 Gate

覆盖去重/越界、多 query 归因、单次 Rerank、未授权整单拒绝、公平配额、失败策略和单库兼容。
Gate：结果确定且可溯源、权限 fail-closed、单库行为兼容、评测变化获批准。

回滚：capabilities 关闭多查询/多库并把上限降为 1；保留字段解析，避免 Schema 回退破坏客户端。

