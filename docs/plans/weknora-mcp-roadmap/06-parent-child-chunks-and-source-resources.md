# 任务 06：父子分块、上下文与来源资源

> 状态：已完成；前置：任务 05 Gate 已通过；后继：任务 07

## 目标与模型

实现 small-to-big 和 MCP Resource。Chunk 增加 parent ID、level、heading path、source span、document/chunk
version；Asset 包含稳定 ID、document/chunk 关联、MIME、size、checksum 和 locator，绝不对外返回绝对路径。
本任务首次修改持久索引，必须兼容旧数据并原子切换。

## 原子任务

1. **06.1 契约先行**：定义应用类型、持久化格式、Evidence 可选字段和 migration version；先写 legacy 读取测试。
   提交：`refactor(chunking): define versioned parent-child contracts`。
2. **06.2 父块生成**：Markdown/DOCX 按章节，PDF 按标题区间或页组，PPTX 按页，表格优先整表，无结构文本
   用大递归窗口；首版仅一层。提交：`feat(chunking): generate structure-aware parent chunks`。
3. **06.3 子块索引**：子块进入主检索；父块使用独立 namespace 或不召回；图片子块关联父块和 Asset。
   提交：`feat(index): index child chunks with parent linkage`。
4. **06.4 原子切换**：完整构建单文档新索引，校验后切 active version，再清旧版；失败保持旧版。
   提交：`feat(index): atomically activate document index versions`。
5. **06.5 `get_chunk_context`**：支持 parent/neighbors/组合及 before/after/max_chars，返回关系和截断；引用仍指向
   命中子块。提交：`feat(mcp): add bounded chunk context retrieval`。
6. **06.6 Resource**：注册 `rag://documents/{document_id}/chunks/{chunk_id}` 和 `/assets/{asset_id}`；逐次鉴权、
   MIME/大小限制。提交：`feat(mcp): expose authorized chunk and asset resources`。
7. **06.7 迁移与评测**：legacy parent 为 null；提供按文档 dry-run/rebuild/失败清单，不在启动时全量迁移；比较
   child-only、parent、neighbors。提交：`feat(migration): rebuild documents into parent-child index` 和评测提交。

## 测试与 Gate

覆盖 ID 稳定、格式规则、旧数据、失败切换、删除幂等、Resource 路径遍历/跨库/MIME/预算及 Golden Set。
Gate：迁移失败不影响查询，父子收益获批准，资源无泄漏，扩展上下文不损失精确引用。

回滚：停止新格式写入并切回上一 active version；确认稳定前不得删除任何已生成版本。

## 完成记录（2026-09-16）

- 已冻结 `index_format_version=2`、parent/child/source span/version/asset 契约，并兼容读取 v1 `parent_id/image_ids`；
- 已实现 Markdown/DOCX heading、PDF 页组、PPT 页、整表及无结构窗口的一层确定性 parent，parent 不进入默认召回；
- 已新增 SQLite sidecar staging/active/retired 版本仓库，摄取成功后原子切 active，失败清 staging，旧 active 保留；
- 已贯通 `get_chunk_context`、双 Client、内部 API，以及 chunk/asset MCP Resource templates；动态读取逐次鉴权并拒绝路径遍历、越权、危险 MIME 和超大资产；
- 已提供 `scripts/rebuild_parent_chunks.py` 单文档 dry-run/apply 迁移入口；旧数据不在启动时全量迁移；
- 最终 Gate：核心单元/契约 148 passed；摄取/存储回归 74 passed；内部 API 18 passed；真实双传输、CLI 与 Golden Set 31 passed、2 skipped；其余鉴权/Web/分页性能集成 57 passed。契约库存为 9 工具，compileall 与 `git diff --check` 通过。检索仍只召回 child，因此 Golden Set 指标无变化。
