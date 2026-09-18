# 任务 03：文档与 Chunk 读取原语

> 状态：已完成
> 前置：任务 02 Gate 通过  
> 后继：任务 04

## 1. 目标

让外部 Agent 在不执行搜索的情况下发现文档、精确读取 Chunk 和检查文档元数据，形成稳定的证据钻取路径。

## 2. 对外工具面

### `list_documents`

输入：collection、q、tag/folder/file/status/time 过滤、排序、分页。输出有界文档摘要，不内联 Chunk 正文。

### `get_chunk`

输入必须同时带 document ID 和 chunk ID，兼容期可接受 `doc_id`。输出完整 Evidence、前后 Chunk ID、
parent ID（本阶段为 null 也合法）、source locator 和 asset IDs。

### `get_document`

保留现有工具，补充可选版本、标签、文件夹、来源、解析器、索引状态、内容类型和更新时间；禁止无界内联 Chunk。

## 3. 原子任务

### 03.1 下沉文档发现服务

从现有 Web 文档列表能力提取 transport-neutral query service，保留 SQL 过滤、稳定排序和分页；Web 路由改为委托它，
行为不得变化。

提交：`refactor(documents): share scoped document discovery service`

### 03.2 扩展只读 Client 接口

在 in-process 和 HTTP Client 同时增加 `list_documents`、`get_chunk`；内部 HTTP API 使用版本化只读端点、共享
API Key 和 scope header。不得给 HTTP Client 增加通用任意请求逃逸口。

提交：`feat(readonly): add scoped document and chunk reads`

### 03.3 注册 `list_documents`

定义输入上限、排序枚举和重复 tag 参数的 AND/OR 映射。未知/越权 collection 沿用既有隐藏策略。

提交：`feat(mcp): add list_documents tool`

### 03.4 注册 `get_chunk`

复用现有稳定排序和 source locator；先解析文档归属并鉴权，再验证 Chunk 属于该文档，防止枚举。

提交：`feat(mcp): add exact chunk read tool`

### 03.5 整理 `get_document` 与兼容层

新增字段只可选追加；保留 `get_document_summary` 和 `doc_id`；记录弃用提示但不在本阶段删除。

提交：`feat(mcp): enrich document metadata compatibly`

### 03.6 端到端测试和文档

覆盖 stdio 与 HTTP Client 两条路径、scope header、逗号 collection 名、旧 Chunk 无 index、空页和超限。

提交：`test(mcp): verify document discovery and chunk drilldown`

## 4. 权限与错误要求

- collection 授权、document 归属、chunk 归属三层复检；
- 不存在与无权访问对调用方保持相同错误；
- 任何 DTO 不得含 `source_path` 绝对路径；
- 后端不可用返回 upstream error，不返回空列表；
- 完整正文只由 `get_chunk` 返回，列表只返回预览。

## 5. 测试与验收

- 过滤、排序、首/中/尾页和越界分页；
- 跨库 document、错文档 chunk、含逗号 scope、恶意 ID；
- previous/next 使用统一稳定顺序；
- in-process 与 HTTP 输出等价；
- 现有 Web 文档列表回归；
- MCP 工具契约和 capability 同步。

## 6. Gate 与回滚

Gate：Agent 可完成 collection → document → chunk 的只读钻取；旧工具全部兼容；响应预算和安全测试通过。

回滚：注销两只新工具并回退 Client 扩展；共享应用服务可以保留，因为 Web 行为已由回归测试锁定。

## 7. 实施记录（2026-09-16）

- 复用 `DocumentService` 的 SQL 分页查询，并补充轻量 document key 入口，形成 transport-neutral 文档发现服务；Web 既有行为不变。
- 两种 `RagReadOnlyClient` 实现和版本化内部 API 均增加 `list_documents`、`get_chunk`；HTTP 继续透传编码后的 collection scope header。
- `list_documents` 提供 collection、文本、状态、文件类型、文件夹、tag AND/OR、更新时间、稳定排序和有界分页；不返回 Chunk 正文或绝对路径。
- `get_chunk` 依次校验 document 归属、collection 权限和 chunk 归属，返回全文、稳定邻居、parent、source locator 与 asset IDs。
- `get_document` 可选追加 version/folder/parser/index status/content type/updated time；保留 `doc_id` 和 `get_document_summary`。
- 真实注册表现为 7 个只读工具；契约清单、Schema 快照和 capabilities 已同步，两个新 feature flag 为 true。

### Gate 结果

- Task 03 定向单元与契约测试通过；`python -m compileall -q src` 通过；`scripts/mcp_contract_inventory.py --check` 通过。
- 双传输测试改为使用临时配置与临时认证数据库，并显式禁用 localhost 请求的环境代理；stdio/HTTP capabilities 一致性与 Web 分页性能 Gate 合计 8 项通过。
- 全量单测另有与本任务无关的既存失败（LLM 消息格式、代理 URL、MCP SDK `mime_type` 字段）；定向 Gate 不受影响。
