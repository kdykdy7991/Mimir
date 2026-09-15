# MCP 知识基础设施开发任务书索引

> 总路线：[`docs/plan-2026-09-15-weknora-mcp-capability-roadmap.md`](../../plan-2026-09-15-weknora-mcp-capability-roadmap.md)  
> 执行方式：严格串行；前一任务书 Gate 未通过，不启动后一任务书。  
> 状态：待实施

## 任务划分

| 顺序 | 任务书 | 覆盖总路线 | 核心交付 |
| ---: | --- | --- | --- |
| 01 | [架构边界、现状契约与检索基线](01-architecture-contract-and-retrieval-baseline.md) | Phase 0–1 | ADR、现有 MCP 快照、Golden Set、评测器 |
| 02 | [MCP 公共契约与能力发现](02-mcp-public-contracts-and-capabilities.md) | Phase 2 | Evidence/Filter/Error/Warning、capabilities |
| 03 | [文档与 Chunk 读取原语](03-document-and-chunk-read-primitives.md) | Phase 3 | `list_documents`、`get_chunk`、`get_document` 整理 |
| 04 | [统一检索与治理过滤](04-search-and-governance-filters.md) | Phase 4–5 | `search_chunks`、模式选择、过滤、诊断 |
| 05 | [多查询与多知识库检索](05-multi-query-and-multi-collection.md) | Phase 6 | `alternate_queries`、授权、跨库融合、部分失败 |
| 06 | [父子分块、上下文与来源资源](06-parent-child-chunks-and-source-resources.md) | Phase 7–8 | small-to-big、`get_chunk_context`、MCP Resource |
| 07 | [知识版本治理与摄取增强](07-knowledge-versioning-and-enrichment.md) | Phase 9–10 | 编辑/版本/Diff/回滚、局部重建、派生索引 |
| 08 | [任务治理、审计与外部同步](08-task-governance-audit-and-sync.md) | Phase 11–12 | Worker、死信、限流、审计、Connector/SSRF |

## 全局约束

- SKDY-RAG 只做 MCP Server，不实现 Agent Core、MCP Client 或任何 LLM/tool loop。
- MCP 默认只读；本任务集不新增写工具。
- 外部 Agent 负责问题理解和查询扩展；服务端只接受可选 `alternate_queries`。
- 所有检索/索引改动必须通过任务 01 建立的 Golden Set 回归。
- 所有权限条件只能缩小 API Key 的 collection 授权范围。
- 应用层不依赖 MCP；MCP 层只做认证、校验和 DTO/Resource 映射。

## 状态维护

每份任务书实施时，在文件顶部把状态改为“进行中”，并追加：提交、变更文件、测试命令、结果、偏差、
遗留问题和回滚验证。Gate 通过后改为“完成”，再启动下一份任务书。

