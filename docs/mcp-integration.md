# MCP 联调指南（stdio + streamable-http）

> 目标读者：MCP 客户端开发者 / 前端联调。本文提供**两套可重复**的真实联调
> 命令：stdio（桌面客户端路径）与 streamable-http（远程 / HTTP 路径），
> 以及自动化的集成测试入口。
>
> 三个 RAG 工具：`query_knowledge_hub`、`list_collections`、`get_document_summary`。

## 多集合路由（M3）

三个工具现在都使用与 Web API 相同的多集合数据模型，不再只读取
`settings.yaml` 中配置的默认 Chroma collection：

- `query_knowledge_hub` 按 `collection` 参数同时路由到该集合的 Chroma
  向量存储和 BM25 索引；不传时仍使用 `default`。
- `list_collections` 以 `data/db/bm25/*.json` 和配置中的默认集合为已知集合，
  并逐个查询对应 Chroma collection 的真实向量数。该调用只读，不会为了
  统计而创建不存在的 Chroma collection。
- `get_document_summary` 接受 Web API 返回的稳定文档 UUID。服务先通过
  `data/db/ingestion_history.db` 将 UUID 解析为 `(collection, source_path)`，
  再到正确的 collection 中读取文档 chunks。因此非默认集合中的文档也可查询。

> `get_document_summary.doc_id` 不是 chunk ID，也不是文件路径。应使用 Web API
> 文档列表/详情中的 `id`。旧数据若没有成功摄取记录，需重新摄取后才能通过 UUID
> 跨集合解析。

## 0. 错误约定（MCP 2.0）

| 场景 | 客户端看到 |
|---|---|
| 已知参数/业务错误（空 `query`、`doc_id` 不存在） | `CallToolResult.is_error == True`（工具级结果,可继续对话） |
| 未知工具名 | 协议级 `MCPError`（JSON-RPC 错误,客户端抛异常） |
| 意外异常（崩溃/上游失败） | 协议级 `MCPError` |

实现见 `src/mcp_server/protocol_handler.py` 的「Error mapping」；测试用例见
`tests/integration/test_mcp_server.py`。

---

## 1. stdio 联调（默认路径）

### 1.1 启动服务器（被客户端作为子进程拉起）

```bash
cd /home/hello/workspace/SKDY-RAG-SERVER
source .venv/bin/activate
python -m main --config ./config/settings.yaml --log-level WARNING
```

### 1.2 Python 客户端（三个真实工具）

```python
import asyncio, sys
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "main", "--config", "./config/settings.yaml",
              "--log-level", "WARNING"],
        cwd="/home/hello/workspace/SKDY-RAG-SERVER",
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.server_info.name)

            # 1) list_collections
            r = await session.call_tool("list_collections", arguments={})
            print("list_collections is_error:", r.is_error)
            print(r.content[0].text)

            # 2) get_document_summary —— 已知业务错误 → is_error=True
            r = await session.call_tool(
                "get_document_summary",
                arguments={"doc_id": "definitely-not-a-real-id"},
            )
            print("get_document_summary is_error:", r.is_error, "|", r.content[0].text)

            # 3) query_knowledge_hub —— 真实检索（dense+sparse+fusion）
            r = await session.call_tool(
                "query_knowledge_hub",
                arguments={
                    "query": "vector search",
                    "collection": "product-docs",
                    "top_k": 5,
                },
            )
            print("query_knowledge_hub is_error:", r.is_error)
            print(r.content[0].text[:200])

asyncio.run(main())
```

### 1.3 自动化

```bash
pytest tests/integration/test_mcp_server.py -v   # 8 个 case,真实 stdio 子进程
```

---

## 2. streamable-http 联调（HTTP 路径）

### 2.1 启动服务器

```bash
cd /home/hello/workspace/SKDY-RAG-SERVER
source .venv/bin/activate
python -m main --transport streamable-http --host 127.0.0.1 --port 8765 --log-level WARNING
# 探活:curl http://127.0.0.1:8765/health   → {"status":"ok","transport":"streamable-http"}
```

### 2.2 curl 快速探活（initialize,SSE 响应）

```bash
curl -sS -X POST http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-03-26","capabilities":{},
                 "clientInfo":{"name":"curl","version":"0"}}}'
```

### 2.3 Python 客户端（三个真实工具,与 stdio 同一套调用）

```python
import asyncio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

BASE = "http://127.0.0.1:8765/mcp"

async def main():
    async with streamable_http_client(BASE) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.server_info.name)

            r = await session.call_tool("list_collections", arguments={})
            print("list_collections is_error:", r.is_error)

            r = await session.call_tool(
                "get_document_summary",
                arguments={"doc_id": "definitely-not-a-real-id"},
            )
            print("get_document_summary is_error:", r.is_error, "|", r.content[0].text)

            r = await session.call_tool(
                "query_knowledge_hub",
                arguments={
                    "query": "vector search",
                    "collection": "product-docs",
                    "top_k": 5,
                },
            )
            print("query_knowledge_hub is_error:", r.is_error)
            print(r.content[0].text[:200])

asyncio.run(main())
```

### 2.4 自动化

```bash
pytest tests/integration/test_streamable_http_cli.py -v
# 真实子进程 `python -m main --transport streamable-http`,覆盖三个工具 + /health
```

---

## 3. 两套自动化入口一览

| 传输 | 命令 | 覆盖 |
|---|---|---|
| stdio | `pytest tests/integration/test_mcp_server.py` | initialize / tools/list / 三个工具调用 / 错误约定 / stderr 纯净 |
| HTTP | `pytest tests/integration/test_streamable_http_cli.py` | 真实子进程启动 / /health / 三个真实工具 / is_error |
| 单测 | `pytest tests/unit/test_protocol_handler.py` | 注册表 / dispatch / is_error 透传 |

> 数据准备：`python scripts/ingest.py --path <pdf> --collection product-docs`（或
> Web API 上传）。查询时的 `collection` 必须与摄取目标一致。无数据时
> `query_knowledge_hub` 返回空/降级结果（`is_error=False`）。
