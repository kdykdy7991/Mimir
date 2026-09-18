# Task 06 开发契约：父子分块、上下文与来源资源

> 状态：已完成
> 日期：2026-09-16
> 前置：Task 05 Gate 已通过

## 1. 交付目标

在保持现有 child-only 索引可读的前提下，引入一层结构化 parent、文档索引版本和有界上下文读取；新增 chunk/asset MCP Resource。搜索仍只召回 child，引用仍指向命中 child，parent 与 neighbors 只作为显式请求的补充上下文。

## 2. 现状审计

- `ChunkRecord` 目前依赖松散 metadata；视觉转换已写入 `parent_chunk_id`，而搜索映射读取 `parent_id`，命名尚未统一；
- `EvidenceV1` 已预留 `document_version/chunk_version/parent_chunk_id/heading_path/asset_ids`，但未形成持久化不变量；
- `ChunkDetail` 已有 previous/next/parent/source locator/asset IDs，可作为上下文服务的安全基础；
- 当前 MCP Resource 仅有静态 capabilities URI，尚无动态模板、逐次授权或二进制资源预算；
- 摄取流程没有“完整构建版本 → 校验 → active pointer 切换”的文档级原子协议。

## 3. 冻结持久化契约

- `index_format_version=2`；旧 chunk 缺失该字段视为 v1，parent/version/heading path 读取为 null/空，不启动时迁移；
- child metadata 使用 `parent_chunk_id`、`chunk_level=child`、`heading_path[]`、`source_span`、`document_version`、`chunk_version`、`asset_ids[]`；兼容读取旧 `parent_id/image_ids`，新写入只用规范字段；
- parent 使用确定性 ID，`chunk_level=parent`，首版最多一层且不得进入默认召回 namespace；
- Asset 包含稳定 ID、document/chunk/version、MIME、byte size、SHA-256、相对 locator；任何契约均不得携带绝对路径。

## 4. 生成规则与原子切换

- Markdown/DOCX 按章节，PDF 按标题区间或有界页组，PPTX 按页，表格优先保持整表；无结构文本使用有重叠的大窗口；
- parent 与 child ID 由 collection、document stable ID、版本和规范化位置派生，同一输入/配置重建必须稳定；
- 单文档新版本先写 staging namespace，校验 child-parent 引用、顺序、资产 checksum 和计数，再以一个事务切 active version；
- 失败只清 staging，旧 active version 保持可查询；旧版本仅在成功切换且通过保留策略后清理；删除必须幂等。

## 5. 上下文与 Resource

- 新增 `get_chunk_context(document_id, chunk_id, include=parent|neighbors|both, before, after, max_chars)`；先验证 document scope 和 chunk ownership，再读取 active version；
- 返回命中 child、可选 parent、前后邻居、关系与显式 truncation；`max_chars` 是整个上下文预算，不能静默裁剪；
- Resource 模板：`rag://documents/{document_id}/chunks/{chunk_id}` 与 `rag://documents/{document_id}/assets/{asset_id}`；每次 read 从请求上下文重新鉴权，不缓存 principal；
- URI 参数只接受稳定 ID，不接受路径、`.`/`..`、编码斜杠；asset MIME 白名单、单资源字节上限和 checksum 必须在读取前验证；错误保持 not-found-or-not-accessible 同形。

## 6. 迁移与 Gate

- 提供按 document 的 dry-run/rebuild 命令和失败清单，不做启动时全量迁移；
- 测试覆盖确定性 ID、各格式规则、v1 兼容、staging 失败、原子切换、删除幂等、上下文预算、路径遍历、跨库授权、MIME/大小/checksum；
- 对 child-only、parent、neighbors 运行 Golden Set 与延迟对比，收益或回归必须记录；
- capabilities 仅在真实工具/Resource handlers 注册后启用 `parent_child_chunks/source_assets/chunk_context_resources`。

## 7. 回滚

停止 v2 写入并将 active pointer 切回上一已校验版本；禁用动态 Resource 与上下文工具，但保留 v2 兼容读取。在迁移 Gate 完成前禁止不可恢复地删除 v1 或上一 active 版本。

## 8. 实施记录（2026-09-16）

本契约已完成实现。当前工具数为 9，并提供两个逐次鉴权的动态 Resource 模板。最终 Gate：核心单元/契约 148 passed，摄取/存储 74 passed，内部 API 18 passed，真实双传输/CLI/Golden Set 31 passed、2 skipped，其余鉴权/Web/性能集成 57 passed。
