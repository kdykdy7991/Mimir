# MCP Server 客户端接入信息

本文是给 MCP Client 使用方的一页式接入清单。独立容器部署时，客户端只连接
MCP Server，不直接访问主 RAG API 或 `/internal/mcp/v1`。

## 连接参数

| 项目 | 值 |
| --- | --- |
| 协议 | MCP Streamable HTTP |
| MCP URL（服务器本机） | `http://127.0.0.1:8765/mcp` |
| MCP URL（局域网/远程） | `http://<SERVER_IP_OR_DOMAIN>:8765/mcp` |
| 匿名健康检查 | `GET http://<SERVER_IP_OR_DOMAIN>:8765/health` |
| 认证 Header | `Authorization: Bearer <MCP_CLIENT_API_KEY>` |
| 工具数量 | 5 个只读工具 |

生产或跨主机接入应使用 TLS 反向代理，将公开地址配置为
`https://<DOMAIN>/mcp`，并透传 `Authorization`、`Mcp-Session-Id` 及
SSE/MCP 响应头。不要直接把内部主 API 暴露给客户端。

## 两种密钥不要混用

- `MCP_INTERNAL_API_KEY`：MCP Server 调用主 API 的服务间共享密钥。只配置在
  API/MCP 容器环境中，不能交给 MCP Client。
- `MCP_CLIENT_API_KEY`：通过 `scripts/mcp_keys.py` 签发的外部 Bearer Key。
  每个客户端单独签发，并绑定允许访问的 collection。

## 服务端签发客户端 Key

先确认要授权的 collection，例如 `skdy common`，然后在项目根目录执行：

```bash
mkdir -p ./data/mcp
.venv/bin/python scripts/mcp_keys.py create \
  --name my-mcp-client \
  --collections 'skdy common' \
  --data-dir ./data/mcp
```

完整 Key 只显示一次，格式类似：

```text
skdy_mcp_<key_id>.<secret>
```

Compose 将 `./data/mcp` 挂载到独立 MCP 容器，因此 Key 在容器重建后仍然有效，
且 MCP 容器仍无法访问 `./data` 中的 RAG 向量库、文档和摄取数据库。

管理命令：

```bash
.venv/bin/python scripts/mcp_keys.py list --data-dir ./data/mcp
.venv/bin/python scripts/mcp_keys.py rotate --name my-mcp-client --data-dir ./data/mcp
.venv/bin/python scripts/mcp_keys.py revoke --name my-mcp-client --data-dir ./data/mcp
```

## MCP Client 配置模板

不同客户端的配置文件名称略有不同，但核心参数相同：

```json
{
  "name": "skdy-knowledge-query",
  "transport": "streamable-http",
  "url": "http://<SERVER_IP_OR_DOMAIN>:8765/mcp",
  "headers": {
    "Authorization": "Bearer <MCP_CLIENT_API_KEY>"
  }
}
```

如果客户端使用 `type` 字段，可将 `transport` 替换为：

```json
"type": "http"
```

## 最小验收

1. 检查服务健康：

   ```bash
   curl -fsS http://<SERVER_IP_OR_DOMAIN>:8765/health
   ```

2. 在 MCP Client 中连接并执行 `tools/list`，应看到：

   - `list_collections`
   - `query_knowledge_hub`
   - `get_document`
   - `get_document_chunks`
   - `get_document_summary`

3. 调用 `list_collections`，只能看到该 Bearer Key 授权的 collection。
4. 调用 `query_knowledge_hub` 获取检索证据。
5. 尝试未授权 collection，应返回拒绝错误。

更完整的 stdio、Python Client 和 curl 示例见
[MCP 联调指南](mcp-integration.md)。
