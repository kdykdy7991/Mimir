# 任务 02：MCP 公共契约与能力发现

> 状态：待实施  
> 前置：任务 01 Gate 通过  
> 后继：任务 03

## 1. 目标

在增加工具前冻结统一的 Evidence、Filter、分页、预算、Warning 和 Error 模型，并提供机器可读能力发现。
本阶段先建立类型和映射，避免后续工具各自发明响应结构。

## 2. 非目标

- 不实现新检索算法；
- 不增加父子分块和多 collection 行为；
- 不改变旧工具的必填字段或错误语义；
- 不依赖 Chroma、BM25 或特定 MCP transport 的私有类型。

## 3. 契约决策

### Evidence v1

必填：`collection_id`、`document_id`、`chunk_id`、`content_type`、`source_locator`、`scores`、
`matched_queries`。可选：版本、parent ID、标题、正文/预览、heading path、asset IDs、indexed_at。

`scores` 固定包含 dense/sparse/fusion/rerank，未执行为 null；禁止把不同量纲压成含义不明的单一 score。

### Filter v1

支持 collection/document/tag/folder/file/content/source/time；`tag_operator=and|or`；
`include_descendants` 只在 folder 存在时生效。空数组按非法参数处理，字段缺失代表不限制。

### Warning/Error v1

Warning 至少覆盖 truncated、rerank_degraded、legacy_metadata_missing、partial_collection_failure。
Error 至少区分 invalid_request、not_found_or_not_accessible、upstream_timeout、upstream_unavailable、
rate_limited、overloaded。工具级失败不得伪装为空 evidence。

## 4. 原子任务

### 02.1 应用层契约类型

在合适的 application/query contracts 模块定义不可变类型及序列化测试；禁止从 MCP SDK 类型反向依赖。

提交：`refactor(query): add transport-neutral evidence contracts`

### 02.2 MCP DTO 与旧响应映射

实现单一 mapper，把应用层结果映射为 MCP structured content；旧工具继续输出兼容字段，新字段只做可选追加。

提交：`refactor(mcp): centralize evidence response mapping`

### 02.3 预算和分页校验器

统一 top-k、page size、返回字符数、query 数/长度上限。配置启动时校验；请求越界返回稳定错误。

提交：`feat(mcp): enforce bounded evidence responses`

### 02.4 能力发现

优先注册只读 MCP Resource，例如 `rag://server/capabilities`；若当前 SDK/Client 对静态 Resource 支持不足，
使用 `get_server_capabilities`。返回 contract version、工具版本、模式、过滤器、上限、内容类型及尚未支持能力。

提交：`feat(mcp): expose versioned server capabilities`

### 02.5 契约快照与 OpenAPI 对齐

更新 MCP Schema 快照；若 REST 共享 DTO，同步 OpenAPI 与 Web 生成类型。增加测试确保 `scores` null 语义、
路径清洗、时间格式和 unknown optional field 的向前兼容。

提交：`test(contract): freeze evidence and capabilities v1`

## 5. 测试与验收

- 应用类型不导入 MCP/Web 包；
- 旧五工具快照保持兼容；
- 每类 Warning/Error 有序列化测试；
- 超大 top-k、过长 query、超预算正文均被拒绝或显式截断；
- capability 与实际注册工具、配置上限一致；
- Evidence 中不出现绝对路径、Secret 或任意异常堆栈。

## 6. Gate 与回滚

Gate：公共契约和 capability 快照通过；旧客户端集成测试通过；所有限制有单一配置来源。

回滚：新字段均为可选；关闭 capability 注册即可回滚，不删除旧 mapper，直至任务 04 完成统一切换。

