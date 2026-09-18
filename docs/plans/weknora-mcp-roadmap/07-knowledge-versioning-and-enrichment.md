# 任务 07：知识版本治理与摄取增强

> 状态：已完成；前置：任务 06 Gate；后继：任务 08

## 目标与边界

支持人工纠错、Diff、回滚及安全重建索引，再增加可独立关闭和评测的自动标签、摘要、合成问题。编辑只走
管理 REST/Web，不新增 MCP 写工具。Revision 不可变，记录 base、来源、原因、操作者和时间；回滚创建新版本。

## 原子任务

1. **07.1 版本存储**：新增 migration、Repository 和 SQLite 实现；旧文档惰性映射 initial revision。
   提交：`feat(store): add immutable document and chunk revisions`。
2. **07.2 读取与 Diff**：授权版本列表、详情和有大小限制的行级 Diff。
   提交：`feat(api): expose authorized chunk revision history`。
3. **07.3 并发编辑**：base revision/`If-Match`、字段白名单、冲突 409；新版本为 pending_index。
   提交：`feat(api): edit chunks with optimistic concurrency`。
4. **07.4 局部重建**：重建 Dense、BM25、父子和派生索引，全部成功后原子激活；失败保持旧版。
   提交：`feat(index): rebuild and activate edited chunk revisions`。
5. **07.5 回滚与 MCP 版本**：回滚生成新 revision；Evidence 明示版本；旧引用返回历史+`is_current=false` 或
   `stale_reference`，不得静默换内容。提交：`feat(api): rollback knowledge revisions safely` 及 MCP 提交。
6. **07.6 自动标签**：优先既有词表；新标签只做候选；人工/模型分开；保存模型、Prompt、置信度、审核状态。
   提交：`feat(enrich): generate reviewable tag suggestions`。
7. **07.7 摘要/合成问题**：摘要标记 derived；问题独立 namespace，关联原 Chunk，可加权/关闭/删除/重建，
   引用永远回原文。提交：`feat(enrich): index traceable synthetic questions`。
8. **07.8 UI、成本与评测**：Web 实施遵循 `web/AGENTS.md`；统计调用、Token、延迟、成本，Golden Set 验证后
   决定默认开关。提交：`feat(web): manage knowledge revisions and enrichment`。

## 测试与 Gate

覆盖并发编辑、重建失败、Diff/回滚幂等、跨库、历史引用、模型降级、人工标签保护和派生引用。
Gate：人工修改不丢失、激活原子、历史引用明确、增强可清理且评测/成本获批准。

回滚：关闭编辑和增强，active version 切回上一版；版本历史只追加不删除。

## 完成记录（2026-09-16）

- 07.1–07.5：不可变 revision、授权历史/Diff、乐观并发、Dense/BM25/parent/derived 补偿式重建、回滚新版本和 MCP stale-reference 已落地；
- 07.6–07.7：标签建议与派生摘要/合成问题具备 provenance、审核、独立 namespace、启停/删除/重建，引用始终回原文；
- 07.8：Web 管理面板、exact-or-null 调用指标及增强评测 Gate 已完成；增强默认关闭；
- 最终分组 Gate：后端增强/事务 22 passed，OpenAPI/MCP 19 passed，Golden Set/评测 45 passed、1 skipped；Web 107 tests，typecheck/lint/build 通过。OpenAPI 60 paths，MCP 9 tools。
