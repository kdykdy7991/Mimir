# 面向外部 Agent 的只读 MCP Server 优化实施方案

> 状态：待实施
> 目标分支：`feature/weknora-inspired-optimizations`
> 当前项目：`/home/hello/workspace/SKDY-RAG-SERVER`
> 参考项目：`/home/hello/workspace/WeKnora`
> WeKnora 固定参考提交：`3e6010e7cd3937f289cc1dbadc829e71eb1163f4`
> 实施性质：现有 MCP Server 的定点优化、局部迁移和解耦，不是新增 Agent，也不是重写 MCP 体系

## 1. 决策摘要

SKDY-RAG 的 MCP Server 是提供给外部 Agent 使用的只读知识接口。SKDY-RAG 自身不建设 Agent，不生成最终答案，不负责工具编排；知识上传、修改和删除继续只允许通过 RAG 管理台完成。

本轮只交付以下 4 个工具：

1. 保留并规范 `list_collections`；
2. 保留并增强 `query_knowledge_hub`；
3. 将 `get_document_summary` 演进为通用只读文档详情能力；
4. 新增 `get_document_chunks`，分页读取指定文档的已索引片段。

本轮不新增 `search_knowledge`。它与现有 `query_knowledge_hub` 职责重复。

本轮不新增 `get_chunk_context` 和 `get_source_image`。WeKnora MCP Server 没有可直接借鉴的对应工具，当前也没有必要扩大范围。

## 2. 不可突破的产品与架构边界

### 2.1 必须保持

- MCP 对外只读；所有工具不得造成知识库、文档、Chunk、模型或会话状态变更。
- 外部 Agent 自行完成推理、答案生成、引用编排和后续工具调用。
- `query_knowledge_hub` 只返回检索证据，不调用 LLM 生成答案。
- HTTP MCP 请求继续使用现有 MCP API Key，并执行集合白名单授权。
- 不存在或无权限访问的文档统一返回 `document not found or not accessible`，不能泄露资源是否存在。
- `stdio` 与 Streamable HTTP 暴露同一套工具和 Schema。
- 已有工具在兼容期内不得被直接删除或无提示改名。

### 2.2 明确排除

- 内置 Agent、Agent Runtime、Agent 配置和 Agent 工具调用；
- MCP Client、`@MCP` 工具选择、OAuth2 和人工审批；
- Chat、问答生成、摘要即时生成和任何 LLM 调用；
- 上传、URL 导入、文本导入、更新、删除和重建索引；
- 租户、模型、会话、共享知识库等管理工具；
- SSE Chat 流式答案；
- 本轮新造相邻 Chunk 导航或单独图片下载工具；
- 为了复用 WeKnora 而引入其 Go 服务、Agent 模块或写操作代码。

## 3. 当前实现基线

当前 MCP Server 已具备：

- `list_collections`；
- `query_knowledge_hub`；
- `get_document_summary`；
- stdio 和 Streamable HTTP 两种传输；
- MCP API Key 认证及集合级白名单；
- MCP `structuredContent` 输出；
- 已知业务错误使用 `CallToolResult.is_error=true`，未知异常使用协议级错误；
- 检索结果的引用、追踪和用量记录。

关键现状文件：

| 位置 | 当前职责 |
| --- | --- |
| `src/mcp_server/server.py` | 进程入口、传输选择、默认工具注册 |
| `src/mcp_server/protocol_handler.py` | 工具注册、Schema 和错误映射 |
| `src/mcp_server/tools/list_collections.py` | 列出授权集合 |
| `src/mcp_server/tools/query_knowledge_hub.py` | 直接构建检索依赖并执行查询 |
| `src/mcp_server/tools/get_document_summary.py` | 解析文档 UUID 并读取全部 Chunk 汇总详情 |
| `src/mcp_server/auth/` | API Key、请求身份和集合授权 |
| `docs/mcp-integration.md` | 当前 MCP 联调说明 |

当前主要问题：

1. 工具层直接依赖 Embedding、Vector Store、BM25、数据库和配置构建逻辑，MCP 协议适配与 RAG 业务实现耦合较深；
2. `get_document_summary` 名称和返回字段过窄，但实现实际上已经在读取文档详情；
3. 外部 Agent 无法分页读取文档的已索引 Chunk；
4. 工具描述仍有“question/ask”措辞，容易诱导调用方误认为 MCP 会生成答案；
5. 工具返回的分页、上限、稳定标识和兼容策略需要统一。

## 4. WeKnora 借鉴范围

### 4.1 有明确代码参考的部分

| 本项目目标 | WeKnora 参考位置 | 使用方式 |
| --- | --- | --- |
| MCP 到主服务的只读 Client 封装 | `mcp-server/weknora_mcp_server.py::WeKnoraClient` | 借鉴请求封装、认证 Header、SSL、超时、异常处理和线程安全 Session；按本项目接口重写 |
| 集合列表 | `list_knowledge_bases` | 仅借鉴薄工具包装和 Schema 表达；本项目已有权限逻辑必须保留 |
| 文档详情 | `get_knowledge` | 借鉴工具到只读 API 的映射；字段按本项目文档模型定义 |
| 文档片段分页 | `list_chunks` / MCP `list_chunks` | 借鉴 `page/page_size` 契约与薄包装结构；不得迁移 `delete_chunk` |
| MCP 工具定义 | `@mcp.tool()` 工具区 | 借鉴参数类型、工具说明和 Client/Tool 分层，不照搬工具全集 |

### 4.2 不迁移的 WeKnora 代码

- `chat`、`agent_chat`、Agent 查询和答案流聚合；
- Tenant、Model、Session、Agent、Wiki 管理；
- Create、Upload、Update、Delete 等所有写方法；
- OAuth、审批和 MCP Client；
- `delete_chunk`；
- 与本项目集合白名单授权冲突的鉴权逻辑。

### 4.3 来源与许可证要求

若复制或实质性改写 WeKnora MIT 代码，必须：

1. 在文件头注明来源路径和固定参考提交；
2. 更新项目第三方声明；
3. 在提交说明中标明 `adapted from WeKnora`；
4. 不得只改变量名后删除来源记录；
5. 仅借鉴接口思想、未复制表达性代码时，不需要伪造“直接迁移”声明。

## 5. 目标工具契约

### 5.1 `list_collections`

用途：外部 Agent 发现当前凭证允许检索的知识库。

输入：

```json
{}
```

输出至少包含：

```json
{
  "collections": [
    {
      "name": "product-docs",
      "description": "产品文档",
      "document_count": 12,
      "chunk_count": 480
    }
  ],
  "count": 1
}
```

实施约束：

- 只返回当前 MCP Principal 白名单内的集合；
- 统计不可用时字段允许为 `null`，不得为了统计创建存储集合；
- 统一使用 `count`，旧版 `n_collections` 在兼容期内保留；
- 不返回本地磁盘路径、数据库路径或内部 Chroma collection 名称；
- 列表顺序必须稳定。

### 5.2 `query_knowledge_hub`

用途：从指定知识库返回与查询相关的原始证据片段。

输入：

```json
{
  "query": "退款规则是什么？",
  "collection": "product-docs",
  "top_k": 8,
  "rerank": true
}
```

约束：

- `query` 去除首尾空白后长度为 1～2000；
- `top_k` 默认 10，最小 1，最大 50；
- 单集合凭证省略 `collection` 时自动选择；
- 多集合凭证省略时返回可操作的工具级错误；
- 新参数使用正向语义 `rerank`；旧参数 `no_rerank` 至少保留一个发布周期，二者不能同时传入；
- 不生成答案，不添加“结论”字段，不调用 LLM；
- 检索或重排降级必须在 `diagnostics` 中明确，不得静默伪装成完整链路。

结构化输出至少包含：

```json
{
  "query": "退款规则是什么？",
  "collection": "product-docs",
  "count": 2,
  "evidence": [
    {
      "rank": 1,
      "chunk_id": "stable-chunk-id",
      "document_id": "stable-document-id",
      "title": "售后政策",
      "source": "docs/refund.pdf",
      "page": 3,
      "score": 0.91,
      "text": "……"
    }
  ],
  "diagnostics": {
    "degraded": false,
    "reasons": [],
    "trace_id": "..."
  }
}
```

兼容要求：旧版 `n_results` 和 `citations` 在兼容期内保留，其值分别与 `count` 和 `evidence` 一致。不得将存储层临时 ID 冒充稳定 `chunk_id`。

### 5.3 文档详情工具

目标名称：`get_document`。

输入：

```json
{
  "document_id": "stable-document-id"
}
```

输出至少包含：

```json
{
  "document_id": "stable-document-id",
  "collection": "product-docs",
  "title": "售后政策",
  "document_type": "pdf",
  "source": "docs/refund.pdf",
  "summary": "入库时已有摘要；没有则为空字符串",
  "tags": [],
  "chunk_count": 24
}
```

约束：

- `summary` 只能读取入库时已经存在的数据，MCP 调用时不得生成；
- 不返回绝对文件路径、内部数据库信息或未授权集合名；
- 无权限与不存在统一返回 `document not found or not accessible`；
- `get_document_summary` 作为兼容别名保留至少一个发布周期；
- 旧参数 `doc_id` 继续接受，新工具规范参数为 `document_id`；
- 兼容工具和新工具必须调用同一个 Handler，不得复制业务实现。

### 5.4 `get_document_chunks`

用途：让外部 Agent 分页读取指定文档已经入库的原始 Chunk。

输入：

```json
{
  "document_id": "stable-document-id",
  "page": 1,
  "page_size": 20
}
```

约束：

- `page` 从 1 开始；
- `page_size` 默认 20，最小 1，最大 50；
- 先解析文档所属集合，再执行集合授权，最后读取 Chunk；
- 排序使用入库时的稳定顺序字段；如果当前数据没有稳定顺序字段，必须先补充契约和兼容推导，不得依赖向量数据库自然返回顺序；
- 不支持任意 metadata filter，避免越权探测；
- 不返回 Embedding 向量、内部文件绝对路径和内联图片字节。

输出至少包含：

```json
{
  "document_id": "stable-document-id",
  "page": 1,
  "page_size": 20,
  "total": 24,
  "has_next": true,
  "chunks": [
    {
      "chunk_id": "stable-chunk-id",
      "index": 0,
      "text": "……",
      "page": 1,
      "section": "退款条件"
    }
  ]
}
```

## 6. 目标架构

```text
外部 Agent
    │ MCP stdio / Streamable HTTP
    ▼
MCP Transport + API Key Authentication
    │ request-scoped Principal
    ▼
四个只读 MCP Tool Handler
    │
    ▼
RagReadOnlyClient（稳定接口）
    ├── InProcessRagReadOnlyClient（迁移期默认）
    └── HttpRagReadOnlyClient（独立部署时启用）
             │
             ▼
       主 RAG 只读 HTTP API
             │
             ▼
Application Services / Stores
```

职责：

- MCP Handler：输入校验、调用 Client、把领域结果转换成 MCP Markdown 与 `structuredContent`；
- `RagReadOnlyClient`：定义 MCP 所需的最小只读接口；
- In-process Client：复用当前应用服务，保证迁移阶段行为不变；
- HTTP Client：用于 MCP Server 独立部署，不包含检索算法和存储实现；
- 主 RAG 服务：负责权限最终校验、集合路由、检索、文档与 Chunk 读取。

不得让 MCP Tool Handler 继续直接创建 Embedding、Vector Store、SQLite Store 或读取 Chroma。

## 7. 目标代码结构

```text
src/mcp_server/
├── server.py
├── protocol_handler.py
├── auth/
├── clients/
│   ├── base.py
│   ├── in_process.py
│   ├── http.py
│   ├── models.py
│   └── errors.py
└── tools/
    ├── common.py
    ├── list_collections.py
    ├── query_knowledge_hub.py
    ├── get_document.py
    └── get_document_chunks.py

src/web_api/
├── routers/mcp_readonly.py
└── schemas/mcp_readonly.py
```

`get_document_summary.py` 在兼容期内可以保留为薄注册模块，但不得继续保存独立查询实现。

## 8. 分阶段实施计划

每个编号任务必须可以独立提交。单个提交只解决一个可验证问题；不得将重构、功能、新依赖和文档更新揉在同一提交。

### Phase 0：固定基线和边界

#### P0.1 记录现有工具契约

- 保存三个现有工具的 `tools/list` Schema 快照；
- 保存正常结果、空结果、参数错误和越权结果样例；
- 覆盖 stdio 与 Streamable HTTP；
- 不修改生产代码。

验收：快照可由测试重复生成，现有测试全部通过。

#### P0.2 添加只读不变量测试

测试四类禁止行为：

- 工具列表中不存在 create/upload/update/delete/import/chat/agent；
- 工具调用前后知识库、文档和 Chunk 数量不变；
- MCP 工具模块不导入 LLM/Agent 模块；
- 越权文档与不存在文档返回同形错误。

建议提交：`test(mcp): pin readonly tool surface and compatibility baseline`

### Phase 1：建立只读 Client 边界

#### P1.1 定义领域模型和接口

在 `clients/models.py` 定义：

- `CollectionInfo`；
- `EvidenceItem` / `KnowledgeQueryResult`；
- `DocumentInfo`；
- `DocumentChunk` / `DocumentChunkPage`。

在 `clients/base.py` 定义只读 Protocol：

```python
class RagReadOnlyClient(Protocol):
    def list_collections(self, principal: Principal) -> list[CollectionInfo]: ...
    def query_knowledge(self, request: QueryRequest, principal: Principal) -> KnowledgeQueryResult: ...
    def get_document(self, document_id: str, principal: Principal) -> DocumentInfo: ...
    def get_document_chunks(
        self, document_id: str, page: int, page_size: int, principal: Principal
    ) -> DocumentChunkPage: ...
```

接口中不得出现 FastMCP/MCP Content 类型、Chroma 类型、Web DTO 或 LLM 类型。

#### P1.2 实现 In-process Client

- 将三个现有工具中的业务查询下沉到 `InProcessRagReadOnlyClient`；
- 保留现有集合授权、检索、重排、Trace 和用量记录；
- Handler 只保留输入校验和输出格式化；
- 注入 Client，不允许 Handler 内部重新构造完整依赖栈；
- 先保证输出与 Phase 0 快照一致。

#### P1.3 统一错误类型

至少定义：

- `InvalidRequestError`；
- `ResourceNotFoundError`；
- `AccessDeniedError`；
- `UpstreamUnavailableError`；
- `UpstreamTimeoutError`。

映射规则：前三类转换为可操作的工具级错误；后两类和未知异常按现有协议规则转换，且日志不得包含凭证。

建议提交拆分：

1. `refactor(mcp): add readonly client contracts`
2. `refactor(mcp): route collection listing through readonly client`
3. `refactor(mcp): route knowledge query through readonly client`
4. `refactor(mcp): route document lookup through readonly client`
5. `refactor(mcp): unify readonly client errors`

### Phase 2：规范现有工具

#### P2.1 规范 `list_collections`

- 删除输出中的磁盘路径和内部存储名称；
- 增加 `document_count`、`chunk_count`；不可用时返回 `null`；
- 增加 `count`，兼容保留 `n_collections`；
- 补齐 Schema、Markdown 和结构化输出一致性测试。

#### P2.2 增强 `query_knowledge_hub`

- 修正文案为“检索知识证据”，禁止“ask/answer”含义；
- 增加 query 长度校验；
- 引入 `rerank` 正向参数并兼容 `no_rerank`；
- 统一稳定 `document_id`、`chunk_id`、rank、title、source、page、score、text；
- 增加 `diagnostics.degraded/reasons/trace_id`；
- 兼容保留 `n_results/citations`；
- 保证没有 LLM 调用。

#### P2.3 文档详情演进

- 将核心 Handler 移入 `get_document.py`；
- 注册新工具名 `get_document`；
- `get_document_summary` 注册为兼容别名，共用同一个 Handler；
- 支持 `document_id`，兼容旧 `doc_id`；
- 禁止即时摘要生成；
- 增加 collection 字段并清理内部路径。

建议每个小节独立提交并单独运行对应单测。

### Phase 3：新增文档 Chunk 分页

#### P3.1 确认稳定排序和稳定 ID

- 审计当前 Chunk metadata 是否存在稳定的 `chunk_index` 或等价字段；
- 审计 `chunk_id` 是否跨进程、跨查询稳定；
- 若缺失，先在应用服务读取层增加兼容推导；
- 本阶段不得重写历史索引；旧数据必须可读。

此任务完成前不得实现分页切片，否则容易产生重复页或漏页。

#### P3.2 Client 增加分页读取

- 解析稳定文档 ID 到 `(collection, source_path)`；
- 授权后按文档过滤读取 Chunk；
- 使用稳定顺序排序后分页；
- 返回 `total/page/page_size/has_next/chunks`；
- 页码越界返回空 `chunks`，不是服务异常。

#### P3.3 注册 MCP 工具

- 添加严格 JSON Schema；
- 添加 Markdown 人类可读输出；
- 添加 structuredContent Schema；
- 添加无权限、不存在、第一页、中间页、末页、越界页和 page_size 上限测试。

建议提交：

1. `test(mcp): pin stable document chunk ordering`
2. `feat(mcp): add readonly document chunk pagination client`
3. `feat(mcp): expose get_document_chunks tool`

### Phase 4：HTTP 解耦

本阶段的目标是让 MCP Server 可以独立部署。不得在前面工具契约未稳定时提前实施。

#### P4.1 主服务提供专用只读 API

添加 MCP 专用只读端点，建议版本化前缀：

```text
GET  /internal/mcp/v1/collections
POST /internal/mcp/v1/query
GET  /internal/mcp/v1/documents/{document_id}
GET  /internal/mcp/v1/documents/{document_id}/chunks?page=1&page_size=20
```

要求：

- 路由只允许 GET/POST 查询语义，不提供任何变更端点；
- 必须复用同一个 `RagReadOnlyClient`/应用服务，不复制检索实现；
- 主服务必须重新校验调用身份和集合范围，不能只信 MCP 传来的 collection；
- 内部 API 不对公网直接暴露；
- 错误响应使用稳定 code，不把 Python 堆栈返回给 MCP；
- OpenAPI 中明确标注 internal/read-only。

#### P4.2 实现 `HttpRagReadOnlyClient`

借鉴 WeKnora Client，但按本项目实现：

- 统一 base URL；
- 显式 connect/read/total timeout；
- 默认验证 TLS；
- 有界连接池；
- 复用 Session/Client；
- API Key 或内部服务凭证通过 Header 传递；
- 禁止记录 Authorization Header；
- 仅实现四个只读方法；
- 禁止通用 `request(method, path)` 直接暴露给 Tool，以免未来绕过只读边界。

#### P4.3 Client 工厂和切换开关

配置建议：

```yaml
mcp_server:
  rag_client_backend: in_process
  rag_api_base_url: http://api:8000
  request_timeout_seconds: 30
```

- 默认先保持 `in_process`，不改变本地开发行为；
- 独立 MCP 镜像使用 `http`；
- backend 配置非法或 HTTP API 启动探测失败时 fail-fast；
- 不允许自动静默回退到 in-process。

#### P4.4 独立部署验证

- MCP 进程镜像中不安装向量数据库、Embedding、Reranker 和文档解析依赖；
- MCP 容器无法直接访问 RAG 数据目录；
- 只通过主服务 HTTP API 获取数据；
- `docker compose` 健康检查验证 MCP 协议初始化和主 API 就绪；
- 中断主 API 时，MCP 返回明确的上游不可用错误。

建议提交拆分：

1. `feat(api): expose versioned internal readonly mcp endpoints`
2. `feat(mcp): add bounded readonly http client`
3. `feat(mcp): add explicit rag client backend selection`
4. `build(mcp): isolate standalone mcp runtime dependencies`
5. `test(mcp): verify standalone readonly http deployment`

### Phase 5：兼容、文档和交付

#### P5.1 兼容期规则

至少一个发布周期内保留：

- `get_document_summary` 工具名；
- `doc_id` 输入参数；
- `n_collections`；
- `n_results` 和 `citations`；
- `no_rerank`。

兼容字段必须标记 deprecated，但不能只写文档不写测试。删除必须另开任务，并以真实调用方迁移完成为前提。

#### P5.2 更新文档

至少更新：

- `docs/mcp-integration.md`；
- MCP 工具列表及调用示例；
- MCP API Key 权限说明；
- 独立部署配置；
- OpenAPI；
- 第三方归因文件（仅在实际复制 WeKnora 代码时）。

#### P5.3 最终交付证据

交付记录必须包含：

- 每个 Phase 的提交哈希；
- 新旧工具 Schema 对比；
- 单测、集成测试和容器烟测命令及结果；
- 兼容字段清单；
- 未完成项和真实阻塞；
- 工作区状态；
- 未经实际验证的项目必须标为 `not run`，不得写成通过。

## 9. 测试与验收矩阵

| 场景 | 预期结果 | 类型 |
| --- | --- | --- |
| 工具发现 | 只出现批准的 4 个主工具及 1 个兼容别名 | 单元 + stdio/HTTP 集成 |
| 只读边界 | 无 create/upload/update/delete/chat/agent 工具 | 单元 |
| 单集合凭证查询 | 省略 collection 自动选择授权集合 | 集成 |
| 多集合凭证查询 | 省略 collection 返回工具级错误 | 集成 |
| 越权集合 | 不执行检索，返回拒绝 | 集成 |
| 越权文档探测 | 与不存在文档同形错误 | 集成 |
| query 空白/超长 | `is_error=true`，不进入检索 | 单元 |
| top_k 越界 | Schema 或 Handler 拒绝 | 单元 + 集成 |
| Reranker 不可用 | 返回检索证据并标记 degraded | 集成 |
| LLM 依赖 | MCP 查询路径没有 LLM 调用 | 单元/依赖边界测试 |
| 文档详情 | 返回已有 metadata，不生成摘要 | 单元 + 集成 |
| Chunk 第一/末/越界页 | 分页字段和内容正确 | 单元 + 集成 |
| Chunk 顺序 | 重复调用结果顺序一致，无重漏 | 回归测试 |
| HTTP 上游超时 | 映射为稳定错误且不泄露凭证 | 集成 |
| stdio 输出纯净 | stdout 只有 MCP 帧，日志在 stderr | 集成 |
| Streamable HTTP 鉴权 | 无 Key、撤销 Key、跨 Key Session 均拒绝 | 集成 |
| 独立镜像 | 不含检索/解析重依赖，不挂载数据目录仍可运行 | 容器烟测 |
| 兼容调用 | 旧工具名、旧参数和旧字段仍工作 | 回归测试 |

建议验证命令：

```bash
pytest tests/unit/test_protocol_handler.py -v
pytest tests/unit/test_list_collections.py -v
pytest tests/unit/test_get_document_summary.py -v
pytest tests/unit/test_mcp_authorization.py -v
pytest tests/integration/test_mcp_server.py -v
pytest tests/integration/test_streamable_http_cli.py -v
pytest tests/integration/test_mcp_http_access_control.py -v
git diff --check
```

工程师应为新增 Client 和工具补充独立测试文件，不能只扩充一个大型测试模块。

## 10. 完成定义

同时满足以下条件才能宣告本轮完成：

1. MCP 主能力收敛为集合发现、证据检索、文档详情和文档 Chunk 分页；
2. 不存在答案生成、Agent、写操作或 LLM 调用路径；
3. 所有工具经 `RagReadOnlyClient` 调用，不直接构建或访问底层存储；
4. HTTP 独立部署模式下 MCP 镜像与 RAG 重依赖、数据目录解耦；
5. 集合白名单在 MCP 边界和主服务边界均受验证；
6. stdio 与 Streamable HTTP 契约一致；
7. 旧调用方在兼容期内不被破坏；
8. 自动化测试覆盖正常、空结果、无权限、参数错误、降级和上游失败；
9. 所有复制或实质改写的 WeKnora 代码完成 MIT 归因；
10. 文档、OpenAPI、部署示例和实施状态同步更新。

## 11. 明确禁止的实现捷径

- 不得把 `query_knowledge_hub` 改名后保留重复实现；
- 不得用 LLM 生成 `answer` 或临时 `summary`；
- 不得直接删除兼容工具；
- 不得信任客户端提交的 collection 而跳过服务端 Principal 校验；
- 不得使用向量数据库自然顺序实现分页；
- 不得将绝对路径、Embedding、图片字节、API Key 或内部异常返回给 Agent；
- 不得为了独立部署复制一套检索算法到 MCP 包；
- 不得静默从 HTTP Client 回退到 in-process；
- 不得顺手迁移 WeKnora 的写工具、Agent 或会话能力；
- 不得在未运行真实测试时声称 Phase 完成。

## 12. 面向接手工程师的执行规则

1. 开始每个任务前阅读本文对应小节和当前实施状态；
2. 一次只领取一个 `P阶段.编号` 任务；
3. 修改前记录现有行为和相关测试；
4. 每个小任务独立提交，提交信息写清任务编号；
5. 每个提交后运行最小相关测试和 `git diff --check`；
6. 每完成一个任务立即更新实施状态，记录提交哈希、测试命令和结果；
7. 发现边界冲突时停止，不自行扩大到 Agent、LLM 或写操作；
8. 不覆盖或清理工作区中与本任务无关的用户修改；
9. 不删除旧工具或旧字段，除非另有明确发布期指令；
10. 交接时说明“已完成、正在做、下一步、风险、测试状态”，使下一位工程师无需重新调查。

建议新增状态文件：

`docs/implementation-status/readonly-mcp-optimization.md`

状态文件每个任务使用以下格式：

```markdown
### P2.2 增强 query_knowledge_hub

- 状态：todo / in-progress / blocked / done
- 提交：<hash 或 none>
- 修改文件：
- 已运行测试：
- 测试结果：
- 遗留问题：
- 下一步：
```

## 13. 审核重点

实现交回审核时，审核者优先核对：

1. 是否误加答案生成或 Agent 能力；
2. 是否仍有 Tool Handler 直接访问存储或构建检索栈；
3. 文档和 Chunk 是否存在越权探测；
4. 分页是否使用稳定顺序；
5. HTTP 解耦是否真的移除了 MCP 镜像中的重依赖和数据挂载；
6. 兼容别名是否共用实现；
7. Schema、Markdown 和 structuredContent 是否表达一致；
8. WeKnora 迁移代码是否有明确来源与许可证记录；
9. 测试证据是否来自真实执行而非文字声明。
