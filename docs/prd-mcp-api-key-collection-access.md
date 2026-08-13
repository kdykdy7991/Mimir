# HTTP MCP API Key 与知识库访问控制设计

> 状态：待开发
> 日期：2026-08-13
> 目标版本：MCP Access Control v1
> 适用范围：`streamable-http` MCP；`stdio` 行为保持不变

## 1. 背景

当前 MCP 支持多个 collection（知识库），但 HTTP 接入方可自行传入任意
`collection`：查询工具直接使用该参数，列表工具返回全部集合，文档摘要工具也能
通过 `doc_id` 定位任意集合。同时，`/mcp` 的 POST、GET、DELETE 尚无身份认证。

本需求为每个外部 Agent 签发独立 API Key，并在服务端把 Key 绑定到允许访问的
collection 白名单。接入方只持有 Key，不能声明或扩大自己的权限范围。

## 2. 目标与非目标

### 2.1 目标

1. HTTP MCP 的所有协议请求必须使用 Bearer API Key 认证。
2. 支持多个 API Key，每个 Key 具有独立的 collection 白名单。
3. 三个现有 MCP 工具均执行服务端授权，不能通过参数或文档 ID 越权。
4. MCP session 与创建它的 API Key 绑定，其他 Key 不能复用或删除该 session。
5. 支持创建、列出、撤销和轮换 Key，明文 secret 只在创建时展示一次。
6. 日志可以定位接入方和拒绝原因，但不得记录完整 API Key。
7. `stdio` 保持现有兼容性，不要求 Authorization Header。

### 2.2 非目标

- 不实现用户登录、OIDC、Organization、Workspace 或通用 RBAC。
- 不给外部 Agent 开放 Key 管理 HTTP API。
- 不限制 Web API、CLI、Streamlit；本期只保护 HTTP MCP。
- 当前 MCP 工具均为只读，v1 权限粒度仅为“能否读取 collection”。
- 不支持通配符、正则表达式或按文档授权。

## 3. 接入体验

管理员创建接入方及白名单：

```bash
python scripts/mcp_keys.py create \
  --name agent-a \
  --collections hr,policy
```

命令只在本次输出一次完整凭证：

```text
API key created. Store it now; it cannot be shown again.
name: agent-a
key: skdy_mcp_k7G2F4.a-long-random-secret
allowed_collections: hr, policy
```

外部 Agent 使用同一个 MCP URL，并在每次请求中附带：

```http
Authorization: Bearer skdy_mcp_k7G2F4.a-long-random-secret
```

客户端配置示例：

```json
{
  "url": "https://rag.example.com/mcp",
  "headers": {
    "Authorization": "Bearer skdy_mcp_k7G2F4.a-long-random-secret"
  }
}
```

权限完全由服务端记录决定。客户端仍可传 `collection`，但只能传白名单内的值。

## 4. 凭证与存储模型

### 4.1 Key 格式

```text
skdy_mcp_<key_id>.<secret>
```

- `key_id`：公开的随机标识，用于 O(1) 定位数据库记录和安全日志，至少 64 bit
  随机熵。
- `secret`：至少 32 个随机字节，使用 URL-safe Base64 编码。
- 完整 Key 只在创建时返回一次。

Key 是高熵机器凭证，不是用户选择的低熵密码。v1 使用 SHA-256 保存 secret 摘要，
并以 `hmac.compare_digest` 做常量时间比较。这样可按 `key_id` 单条查询，不必在每个
请求中逐条执行昂贵的密码哈希。若未来允许用户自定义短密码，应使用 Argon2id；
本期禁止自定义 secret。

### 4.2 SQLite 存储

使用独立文件 `data/db/mcp_access.db`：

```sql
CREATE TABLE mcp_api_keys (
    key_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    secret_digest TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    last_used_at TEXT
);

CREATE TABLE mcp_api_key_collections (
    key_id TEXT NOT NULL,
    collection TEXT NOT NULL,
    PRIMARY KEY (key_id, collection),
    FOREIGN KEY (key_id) REFERENCES mcp_api_keys(key_id) ON DELETE CASCADE
);
```

约束：

- 白名单至少包含一个明确 collection；v1 不提供 `*`。
- 创建 Key 时校验 collection 当前存在，避免拼写错误。
- collection 后续被删除时授权记录可保留，但不会让不存在的数据重新出现。
- `last_used_at` 限频更新，例如每个 Key 最多每 5 分钟写一次。
- SQLite 文件及备份限制操作系统读权限，不得纳入 Git。

### 4.3 管理命令

新增 `scripts/mcp_keys.py`：

```bash
# 创建，完整 Key 仅输出一次
python scripts/mcp_keys.py create --name agent-a --collections hr,policy

# 只显示元数据，不显示摘要和完整 Key
python scripts/mcp_keys.py list

# 立即撤销
python scripts/mcp_keys.py revoke --name agent-a

# 创建新 Key，并默认立即撤销旧 Key
python scripts/mcp_keys.py rotate --name agent-a
```

命令均支持 `--data-dir`，默认 `./data`。不得提供“查看已有明文 Key”能力。

## 5. HTTP 认证与 session 绑定

### 5.1 认证边界

认证在 Starlette ASGI 层完成，覆盖 MCP endpoint 的全部方法：

- `POST /mcp`：initialize、tools/list、tools/call；
- `GET /mcp`：SSE 流；
- `DELETE /mcp`：结束 session。

`GET /health` 继续匿名开放，只返回存活状态。认证成功后，中间件把不可变的
`AccessPrincipal` 写入当前 ASGI request scope：

```python
@dataclass(frozen=True)
class AccessPrincipal:
    key_id: str
    name: str
    allowed_collections: frozenset[str]
```

当前 MCP SDK 会将 Streamable HTTP 的 Starlette `Request` 放入
`ServerRequestContext.request`。协议处理器从 request scope 取得 principal，再传给
工具分发层。不得使用进程级全局变量保存当前身份，否则并发请求会产生身份串线。

### 5.2 Session 身份绑定

仅验证“请求带有一个有效 Key”不够：其他接入方可能拿自己的有效 Key 配合别人的
`Mcp-Session-Id`，尝试复用、干扰或删除该 session。

认证中间件维护 `session_id -> key_id` 绑定：

1. initialize 成功响应产生 `Mcp-Session-Id` 时，记录当前 `key_id`；
2. 后续携带 session ID 的 POST、GET、DELETE 必须使用相同 `key_id`；
3. 不匹配时返回 403，不把请求交给 MCP session manager；
4. DELETE 成功或 session 过期后删除绑定；
5. 服务重启后内存 session 本身失效，因此绑定无需持久化。

绑定表需异步锁保护，并设置最大容量及过期清理，避免无界增长。撤销检查仍在每个
HTTP 请求执行，不能只在 initialize 时认证。

### 5.3 HTTP 错误

认证失败发生在 MCP JSON-RPC 处理之前：

| 场景 | 状态码 | 响应 |
|---|---:|---|
| 缺少或格式错误的 Authorization | 401 | `{"error":"unauthorized"}` |
| Key 不存在、摘要不匹配、已撤销 | 401 | `{"error":"unauthorized"}` |
| Key 与 MCP session 不匹配 | 403 | `{"error":"forbidden"}` |

401 附带 `WWW-Authenticate: Bearer`。凭证失败统一外部响应，避免泄露 Key 是否存在；
详细原因只写服务端安全日志。

## 6. 工具授权规则

授权必须在实际选择 collection 之前执行，不能依赖工具描述、输入 schema 或客户端
自觉。

### 6.1 `query_knowledge_hub`

- 显式传 `collection`：必须属于 `allowed_collections`。
- 未传 `collection`：白名单只有一个时自动使用它；有多个时返回工具级参数错误，
  要求显式选择；不再隐式回落 `default`。
- 无权限统一返回 `CallToolResult(is_error=True)`：
  `collection is not accessible with this credential`。
- 必须先授权，再构建 embedding、Chroma、BM25 和 reranker。

### 6.2 `list_collections`

- 只返回“服务端现存集合 ∩ 当前 Key 白名单”。
- `n_collections` 使用过滤后的数量。
- 不返回未授权集合的名称、数量、路径或存在性。
- 白名单内集合已经删除时不返回该集合。

### 6.3 `get_document_summary`

- 把 `doc_id` 解析为 `(collection, source_path)` 后立即检查 collection 权限，通过后
  才能访问向量存储。
- “文档不存在”和“文档存在但无权访问”统一返回
  `document not found or not accessible`，防止通过 UUID 探测其他知识库。
- 日志内部可以区分 not-found 与 forbidden，但不得向调用方返回 collection 或路径。

### 6.4 防御纵深

工具内部保留授权校验。后续新增任何读取 collection 的 MCP tool，都必须显式接收
`AccessPrincipal` 并调用统一授权服务；HTTP 模式缺少 principal 时必须 fail closed。

## 7. stdio 兼容策略

`stdio` 没有 HTTP Header，通常由本机客户端作为受信子进程启动。本期保持原行为：
可访问全部 collection，默认 collection 仍为 `default`。

实现应显式区分：

- `streamable-http`：必须存在认证 principal，否则拒绝；
- `stdio`：使用内部 `TrustedLocalPrincipal`，权限为所有现存 collection。

不能使用“principal 为 None 就跳过授权”的逻辑，以免 HTTP 上下文传递故障时放行。

## 8. 配置与部署

在 `Settings` 增加：

```yaml
mcp_access:
  enabled: true
  database_path: "./data/db/mcp_access.db"
  session_ttl_seconds: 86400
  max_sessions: 10000
```

规则：

- HTTP MCP 默认要求开启认证，生产示例必须开启。
- 为兼容本地开发可显式 `enabled: false`；此时若 `--host` 不是
  `127.0.0.1`/`::1`，服务必须拒绝启动。
- 启动时检查数据库可读、schema 可迁移；认证开启但存储不可用时 fail closed。
- Key 本身不写入 YAML 或 `.env`。
- 反向代理必须透传 `Authorization`、`Mcp-Session-Id` 及 MCP 响应头，并使用 TLS。

## 9. 日志与安全要求

安全日志至少包含：事件类型、`key_id`、接入方名称、工具名、授权结果、请求的
collection（仅服务端）以及 session ID 的截断值或摘要。

禁止记录：完整 Authorization Header、完整 API Key、secret、`secret_digest`、查询
结果正文和文档正文。

另外：

- 只接受 HTTPS，应用可在可信反向代理后使用 HTTP。
- 运维层增加按 IP 和 `key_id` 的速率限制；应用 v1 可预留接口。
- 撤销在下一次 HTTP 请求立即生效，包括已有 session。
- digest 比较使用常量时间函数。

## 10. 代码改造清单

建议新增：

```text
src/mcp_server/auth/
├── __init__.py
├── models.py          # AccessPrincipal、KeyRecord
├── key_service.py     # 生成、摘要、认证、撤销、轮换
├── store.py           # SQLite schema 与访问
├── middleware.py      # Bearer 认证、scope 注入、session 绑定
└── authorization.py   # collection 授权与错误语义
scripts/mcp_keys.py
tests/unit/test_mcp_auth_store.py
tests/unit/test_mcp_auth_middleware.py
tests/unit/test_mcp_authorization.py
tests/integration/test_mcp_http_access_control.py
```

建议修改：

```text
src/core/settings.py
config/settings.yaml
src/mcp_server/server.py
src/mcp_server/protocol_handler.py
src/mcp_server/transports/streamable_http.py
src/mcp_server/tools/query_knowledge_hub.py
src/mcp_server/tools/list_collections.py
src/mcp_server/tools/get_document_summary.py
docs/mcp-integration.md
README.md
.gitignore
```

协议处理器应把 principal 作为独立参数传入 dispatch/tool context，不能将 `_principal`
塞进客户端 arguments；内部安全上下文不得与客户端可控参数共用字典。

## 11. 测试计划

### 11.1 单元测试

- Key 格式、随机性、摘要验证、创建、重名、列出、撤销、轮换和 SQLite 重开；
- Header 缺失、非 Bearer、格式错误、未知 Key、错误 secret、撤销 Key；
- `/health` 匿名可用，`/mcp` POST/GET/DELETE 均受保护；
- session 首次绑定、同 Key 复用、跨 Key 拒绝、DELETE 清理、过期清理；
- 单库默认选择、多库要求显式选择、越权 collection 拒绝；
- collection 列表过滤；
- 不存在与无权限文档使用相同外部错误；
- HTTP 缺少 principal 时 fail closed，stdio trusted principal 保持兼容。

### 11.2 集成测试

准备 `hr`、`policy`、`finance` 三个 collection，并签发：Agent A 可读
`hr,policy`，Agent B 可读 `finance`，另准备一个已撤销 Key。

至少验证：

1. 无 Key 无法 initialize；
2. A 的列表只能看到 `hr, policy`；
3. A 可查询 `hr`，不能查询 `finance`；
4. A 未传 collection 时因有两个授权库收到工具级错误；
5. B 未传 collection 时自动使用唯一授权库 `finance`；
6. A 无法读取 finance 文档摘要，响应不泄露文档是否存在；
7. revoked Key 不能新建或继续使用 session；
8. B 携带 A 的 session ID 时收到 403；
9. 两个 Key 并发查询不会发生 principal 串线；
10. 现有 stdio 集成测试继续通过。

### 11.3 回归命令

```bash
pytest tests/unit/test_mcp_auth_store.py \
       tests/unit/test_mcp_auth_middleware.py \
       tests/unit/test_mcp_authorization.py -v

pytest tests/integration/test_mcp_http_access_control.py \
       tests/integration/test_streamable_http_roundtrip.py \
       tests/integration/test_streamable_http_cli.py \
       tests/integration/test_mcp_server.py -v
```

## 12. 验收标准

- 外部 Agent 只需配置 URL 和 Bearer API Key，不需要也不能上传白名单。
- 每个有效 Key 只能看到、查询和读取其白名单内的数据。
- 未认证 `/mcp` 请求不能进入 MCP session manager。
- 已认证但跨 Key 的 session 复用被拒绝。
- 越权响应不泄露 collection 或文档是否存在。
- 撤销 Key 后，已有 session 的下一次请求立即失败。
- 数据库、配置和日志中均不存在完整 API Key。
- `GET /health` 仍可匿名探活。
- `stdio` 的三个现有工具行为和测试不回退。
- 文档包含 Key 发放、客户端配置、撤销、轮换及反向代理注意事项。

## 13. 开发拆分

### 批次 1：凭证与认证边界

Settings、SQLite store、Key 管理 CLI、ASGI Bearer 中间件、scope principal、session
身份绑定及单元测试。

### 批次 2：工具授权

协议上下文传递 principal、三个工具的白名单与防泄露错误、stdio trusted principal
以及工具/协议层测试。

### 批次 3：集成与交付

双 Agent、跨 session、撤销和并发集成测试；更新 MCP 联调指南、README、配置示例；
执行完整回归与部署检查。

## 14. 后续演进

v1 稳定后可增加 Key 过期时间、速率限制、动作权限、管理后台、OIDC/OAuth 2.1、
Organization/Workspace/RBAC 和持久化安全审计。这些能力不阻塞 v1；当前重点是让
HTTP 身份认证、session 绑定及三个工具的 collection 授权形成不可绕过的完整链路。
