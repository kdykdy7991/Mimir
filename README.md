# SKDY RAG Server

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-1.0%2B-purple)](https://modelcontextprotocol.io)
[![Status](https://img.shields.io/badge/status-alpha-yellow)]()

一个可插拔的检索增强生成（RAG）服务，通过 [Model Context Protocol](https://modelcontextprotocol.io) 对外暴露工具接口。系统中的每个组件——LLM、Embedding、向量库、切分器、重排器、文档加载器、评估器——均为可替换的后端实现，统一通过配置文件切换，无需改动代码。

```text
   文档 ──► 摄取管道 ──► 向量库
              │              │
              └─► Trace 存储 │
                             ▼
   MCP 客户端 ──► MCP Server ──► 混合检索 ──► 可选重排 ──► 引用/多模态响应
   (Copilot、                     │               │
    Claude、                      └─► Trace 存储  │
    Cursor)                                       ▼
                                             可观测层
                                          (日志 + Dashboard)
```

---

## 功能特性

- **全链路可插拔** — 所有后端通过 `config/settings.yaml` 切换，无需修改代码；Provider 工厂统一管理注册与实例化。
- **混合检索** — 稠密向量召回（语义）+ BM25 稀疏召回（字面），通过倒数排名融合（RRF）合并结果。
- **多知识库隔离** — 摄取、Web API 与 MCP 查询统一按 collection 路由到独立的 Chroma + BM25 数据，支持中文及非 ASCII 集合名。
- **两段式排序** — 粗排（`top_k_dense` + `top_k_sparse`）后接可选的 Cross-Encoder 或 LLM 精排。
- **多模态摄取** — PDF 按位置抽取图片，Vision LLM 生成描述后拼回相邻 Chunk，复用纯文本检索链路。
- **端到端可观测** — 摄取与查询链路的每一阶段均输出结构化 Trace 记录；Streamlit Dashboard 提供浏览、回放与检视能力。
- **MCP 原生接入** — 通过 stdio 传输对接 GitHub Copilot、Claude Desktop、Cursor 等 MCP 客户端。
- **评估扩展接口** — 已提供评估器抽象、工厂及自定义检索指标；Golden Test Set CLI 与 Ragas 集成尚待接入。

---

## 技术栈

| 层级            | 默认实现                                  | 可选实现                            |
|-----------------|------------------------------------------|------------------------------------|
| LLM             | OpenAI 兼容接口                          | OpenAI、DeepSeek、MiniMax、vLLM、Ollama |
| Embedding       | OpenAI 兼容接口                          | OpenAI、BGE / Sentence-Transformers、HF |
| Vision LLM      | OpenAI 兼容多模态模型                    | 可按模型能力扩展                    |
| 向量库          | ChromaDB（持久化）                        | Qdrant、Pinecone                   |
| 稀疏检索        | BM25（本地持久化索引）                    | Elasticsearch（配置预留）           |
| 重排器          | 关闭                                     | Cross-Encoder、LLM-based           |
| 切分器          | Recursive character                      | Semantic、fixed-length             |
| 文档加载器      | PDF（PyMuPDF + markitdown）、Markdown（.md/.markdown） | 通过 `LoaderFactory`/`LoaderRegistry` 按扩展名扩展 |
| MCP 传输        | stdio + streamable-http（HTTP/SSE）      | —                                  |
| Dashboard       | Streamlit                                | —                                  |

---

## 安装

### 环境要求

- Python 3.10+
- 所选 LLM / Embedding 提供商的 API Key（或本地推理后端）

### 安装步骤

```bash
git clone <repo-url>
cd SKDY-RAG-Server

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env  # 然后填入 API Key
```

> **当前环境注意**：`requirements.txt` 包含 `pymupdf`（PDF 解析依赖），部分 PDF 摄取链路与测试依赖此包。若 `import pymupdf` 失败，先单独执行 `pip install pymupdf`。

校验配置加载：

```bash
python main.py --check
# ✅ 配置加载成功
#    LLM Provider: openai
#    Embedding Provider: openai
#    Vector Store: chroma
```

---

## 配置

所有运行时行为集中在 [`config/settings.yaml`](config/settings.yaml) 一个文件中管理，通过 `${VAR}` 语法引用环境变量。

```yaml
llm:
  provider: openai          # openai | deepseek | minimax | vllm | ollama
  model: gpt-4o
  api_key: "${OPENAI_API_KEY}"
  base_url: "https://api.openai.com/v1"

embedding:
  provider: openai
  model: text-embedding-3-small
  dimensions: 512

vector_store:
  backend: chroma           # chroma | qdrant | pinecone
  persist_path: ./data/db/chroma

retrieval:
  top_k_dense: 20
  top_k_sparse: 20
  top_k_final: 10
  fusion_algorithm: rrf     # rrf | weighted_sum

rerank:
  backend: none             # none | cross_encoder | llm
```

切换 Provider：修改 `settings.yaml` 中对应字段，并设置相应环境变量即可，无需改动代码。

---

## 使用

### 已实现

#### 摄取文档

```bash
python scripts/ingest.py --path ./data/documents --collection default
```

链路：**PDF / Markdown → canonical Markdown → Chunk → Refine → Embed → Upsert**。Markdown 保留标题、列表、表格、引用与 fenced code block；PDF 含图片的页面通过 Vision LLM 生成描述，并合并入相邻 Chunk。所有中间状态写入 Trace 日志。

#### 查询

```bash
python scripts/query.py --query "什么是混合检索?" --top-k 5
```

运行完整检索链路（Dense + Sparse → RRF → 可选 Rerank），输出排序后的 Chunk 及分数。

> 当前响应是检索结果、引用信息及关联图片的组装结果，尚未包含基于上下文的 LLM 最终答案生成步骤。

#### Dashboard

```bash
python scripts/start_dashboard.py
```

启动 Streamlit 多页面应用。目前提供系统总览、数据浏览器、文档入库、入库追踪和查询追踪五个页面。也可以直接运行：

```bash
streamlit run src/observability/dashboard/app.py
```

### Web 前端与 Web API（产品界面）

独立的 Next.js Web 前端 + FastAPI Web API（替代 Streamlit 作为产品 UI）。统一入口与文档：

```bash
# 统一启动（后端 + 前端一起）
./scripts/start_dev.sh

# 自定义端口（可选）
SKDY_MCP_PORT=8876 SKDY_API_PORT=8877 SKDY_WEB_PORT=3100 ./scripts/start_dev.sh

# 或分开启动
make api             # Web API @ 127.0.0.1:8766（Swagger 在 /docs）
make web             # Next.js 前端 @ http://localhost:3000

# 真实依赖健康检查
curl -sS http://127.0.0.1:8766/api/v1/system/health
# -> embedding / chroma / sqlite / bm25 均做真实探测，status 为最差依赖
```

`start_dev.sh` 会同时启动 MCP Streamable HTTP、Web API 和前端，检查三者端口、
同步前后端端口配置，并为本地 API
及内网 embedding 服务补充代理绕过规则。默认同时监听本机和局域网地址；
可设置 `SKDY_PUBLIC_HOST`、`SKDY_API_HOST`、`SKDY_WEB_HOST` 或
`SKDY_EMBEDDING_HOST` 覆盖自动配置。Next.js 开发源默认允许自动检测到的
局域网地址，也可用逗号分隔的 `SKDY_NEXT_ALLOWED_DEV_ORIGINS` 覆盖。
局域网模式没有鉴权，仅应在可信网络使用。
浏览器端的 `/api/*` 由 Next.js 同源转发到 FastAPI，避免局域网代理和跨域差异。

关键入口：
- 前端契约与类型生成：`cd web && npm run gen:types`（读 `docs/openapi/openapi.v0.2.json`）。
- Web API 契约 / 错误码 / 端点速查：**[`docs/web-api-onboarding.md`](docs/web-api-onboarding.md)**。
- 里程碑交接包：`docs/handoff-2026-07-31-m2-batch*.md`、`docs/handoff-2026-08-03-m3-batch*.md`。
- 性能基线：`make benchmark`（查询延迟 p50/p95，预算 P95 < 2s；本机实测 p95 ≈ 25ms）。
- 可选容器部署：`make docker-up`（后端，`network_mode: host` 直达本机 embedding）。

> 说明：产品 UI 已由 Web 前端承担；Streamlit Dashboard 保留为**内部调试工具**，不再是产品界面。

### 并发与部署（重要）

**Web API 必须运行在单个 worker 进程**。原因：BM25 磁盘索引的写锁
（`src/ingestion/storage/bm25_locks.py`）是**进程内**的——它串行化同一进程
内多线程对同一 Collection 的 `load → add/remove → atomic save → cache
invalidate`，但**不**协调多个进程（多 Uvicorn worker）共享 `data/` 的竞争。

- `make api` / `python -m src.web_api.main`：单 worker（uvicorn 默认，入口
  已显式 `workers=1`），**正确用法**。
- ❌ **不要**用 `uvicorn src.web_api.app:app --workers N`（N>1）启动，直到
  引入跨进程锁（文件锁 / DB advisory lock）。
- 容器部署（`docker-compose.yml`）同样单 worker；多副本扩容前必须先落地
  跨进程锁。

### 部分完成

| 命令 | 阶段 | 当前状态 |
|------|------|----------|
| `python scripts/evaluate.py ...` | H | CLI 仍为占位入口；评估器抽象、工厂和 Custom Evaluator 已实现，Golden Test Set 执行链路与 Ragas 尚未接入。 |

MCP 集成已落地（E1-E6），详见下文 [MCP 集成](#-mcp-集成) 章节。

---

## MCP 集成

**当前状态：E1-E6 已实现。** 服务支持 **stdio** 和 **streamable-http** 两种 MCP 传输，并支持文本、结构化引用及多模态 `ImageContent` 返回，可挂载到兼容 MCP 协议的客户端（GitHub Copilot、Claude Desktop、Cursor 或远程 HTTP 客户端）。

**启动**：

```bash
# stdio 模式（默认）—— 被 MCP 客户端作为子进程拉起
python main.py

# streamable-http 模式 —— 远程 / 容器化客户端走 HTTP
python main.py --transport streamable-http
python main.py --transport streamable-http --host 0.0.0.0 --port 8765 --mcp-path /mcp

# 配置校验后退出，不启动 server
python main.py --check
```

**stdIO 客户端接入**（Claude Desktop / Cursor / Cline 等桌面端）：

```json
{
  "mcpServers": {
    "skdy-rag": {
      "command": "/abs/path/to/.venv/bin/python",
      "args": ["-m", "main", "--config", "/abs/path/to/config/settings.yaml"],
      "cwd": "/abs/path/to/SKDY-RAG-SERVER"
    }
  }
}
```

**HTTP 客户端接入**（远程 agent / 容器化部署 / Web demo）：

HTTP MCP 默认启用 API Key 认证。先使用 `python scripts/mcp_keys.py create --name <agent> --collections <collection,...>` 签发凭证；完整 Key 只显示一次。客户端必须在每个请求携带 `Authorization: Bearer <key>`。撤销或轮换命令及完整联调流程见 [`docs/mcp-integration.md`](docs/mcp-integration.md#22-访问控制api-key-认证)。

```python
import asyncio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    import httpx

    http_client = httpx.AsyncClient(
        headers={"Authorization": "Bearer skdy_mcp_<key_id>.<secret>"},
    )
    async with streamable_http_client(
        "http://localhost:8765/mcp", http_client=http_client,
    ) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool(
                "query_knowledge_hub",
                {"query": "公司考勤怎么规定", "top_k": 5},
            )
            print(result.content[0].text)

asyncio.run(main())
```

**已暴露的 Tool**：

| Tool                       | 入参                                            | 出参                                                                 |
|----------------------------|-------------------------------------------------|----------------------------------------------------------------------|
| `query_knowledge_hub`      | `query` (str), `top_k?`, `collection?`, `no_rerank?` | 指定集合的 Markdown 结果 + `structuredContent.citations[]`（含 source/page/chunk_id/score） |
| `list_collections`         | —                                               | 已知集合列表 + 各集合 BM25 chunk / Chroma vector 计数                  |
| `get_document_summary`     | `doc_id` (Web API 稳定 UUID)                    | 跨集合解析后的文档元数据（title/summary/tags/source_path/chunk_count）   |

接口契约见 [`DEV_SPEC.md`](DEV_SPEC.md) §E；客户端接入配置可参考 [`.github/skills`](.github/skills) 中的自动生成示例。
完整联调步骤及多集合路由语义见 [`docs/mcp-integration.md`](docs/mcp-integration.md)。

**约束**：

- **stdio 模式**：所有日志走 stderr，stdout 严格只输出 MCP 帧——避免污染协议流。服务进程被 MCP 客户端以子进程方式拉起，通过 stdin 接收 JSON-RPC 请求。
- **streamable-http 模式**：服务监听 `--host/--port`（默认 `127.0.0.1:8765`），MCP 端点挂在 `--mcp-path`（默认 `/mcp`）。另暴露 `GET /health` 探活端点。**默认绑定 `127.0.0.1`——只接受本机连接**；要接受外部连接需显式 `--host 0.0.0.0` 并放在反向代理之后。
- **外部 HTTP 接入**：保持 `mcp_access.enabled: true`；服务应位于 TLS 反向代理之后，代理必须透传 `Authorization`、`Mcp-Session-Id` 和 SSE/MCP 响应头。认证关闭时服务只允许绑定 loopback 地址。

---

## 项目结构

```text
.
├── config/                  # settings.yaml 与 Prompt 模板
├── data/                    # 文档、图片、持久化向量库
├── logs/                    # Trace 记录（JSONL）
├── scripts/                 # ingest / query / evaluate / dashboard 入口
├── src/
│   ├── core/                # 查询引擎、响应生成、Trace 上下文
│   ├── ingestion/           # PDF → Chunk → Embed 管道
│   ├── libs/                # 可插拔后端（llm、embedding、vector_store…）
│   ├── mcp_server/          # MCP 传输、协议处理器、Tool 与多模态响应
│   └── observability/       # 结构化日志、Trace 与 Streamlit Dashboard
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

每个后端模块遵循统一的目录约定：

```
base_*.py          ← 抽象接口
<provider>_*.py    ← 具体实现
*_factory.py       ← 注册表与按名字实例化
```

新增 Provider（例如新的 LLM）：实现接口、在工厂中注册、在 `settings.yaml` 中添加对应配置项。

---

## 开发

### 运行测试

```bash
pytest                              # 完整测试集
pytest -m unit                      # 仅快速单元测试
pytest -m integration               # 集成测试（需本地依赖）
pytest -m e2e                       # 端到端测试（完整链路）
pytest -m "not network"             # 跳过依赖网络的测试
```

### 端到端冒烟测试

`tests/smoke/run_smoke.py` 跑通 `ingest → query → trace` 的完整链路，
使用一个本地生成的 PDF + `sentence-transformers` embedding（无需任何 API key）。
首次运行会从 HuggingFace Hub 下载 `BAAI/bge-small-zh-v1.5` 模型（~50MB）。

```bash
python tests/smoke/run_smoke.py
```

退出码：
- `0` — 冒烟通过
- `1` — 真实失败（管线错误）
- `77` — 跳过（HuggingFace 不可达 / 模型未缓存）

> 注：当前环境（沙箱）无外网访问 HuggingFace Hub，
> 冒烟会退出码 77。`tests/e2e/` 用 mock 后端覆盖了同样的链路。

### 代码约定

- 所有公共接口位于 `src/` 下；后端实现按模块隔离。
- 配置变更应在 `settings.yaml` 中完成，而非散落在源码里。
- Trace 事件统一通过 `src/observability` 发出，管道代码中不要使用 `print()`。

### 分支策略

| 分支          | 用途                                            |
|---------------|------------------------------------------------|
| `main`        | 稳定发布版本。每个 Release 折成单 Commit。       |
| `dev`         | 活跃开发分支，保留完整 Commit 历史。             |
| `clean-start` | 工程骨架（Skills + DEV_SPEC），用于从零重建。    |

---

## 路线图

以下状态按当前仓库中的实现与入口整理；详细设计目标参见 [`DEV_SPEC.md`](DEV_SPEC.md)。

| 阶段 | 范围                                              | 状态            | 备注 |
|------|---------------------------------------------------|-----------------|------|
| A    | 工程骨架 + 测试基座                                | ✅ 已实现        | 单元、集成、E2E 和 smoke 测试目录均已建立 |
| B    | 可插拔 Libs（LLM / Embedding / Reranker / …）      | ✅ 已实现        | 抽象接口、Provider 实现和 Factory 已落地 |
| C    | 摄取管道（PDF → 向量）                             | ✅ 已实现        | PDF、图片、Chunk、Dense/Sparse 编码及多存储写入已贯通 |
| D    | 检索（混合检索 + Rerank）                          | ✅ 已实现        | Dense + BM25 + RRF + 可选 Rerank，并支持单路降级 |
| E    | MCP Server 与 Tool 定义                            | ✅ 已实现        | stdio、streamable-http、3 个 Tool、结构化引用及多模态返回 |
| F    | Trace 基础设施                                     | ✅ 已实现        | 摄取与查询阶段可写入 JSONL Trace，并由 Dashboard 读取 |
| G    | Streamlit Dashboard                                | ✅ 已实现        | 启动脚本及五个功能页面已落地 |
| H    | 评估体系（Ragas + Custom）                          | 🟡 部分          | 评估器接口与 Custom Evaluator 已落地，CLI / Ragas 未接入 |
| I    | E2E 验收与文档收口                                 | ✅ 已收口        | 单元/集成/契约/E2E 全绿；README + onboarding + 职责进度已同步 |

> 产品界面（Web 前端 + Web API）按 `PRODUCTION_WEB_DEV_SPEC.md` 的 M1–M4 推进，**M1–M4 已全部完成（除 rerank 真实接入）**；详见 [`docs/web-api-onboarding.md`](docs/web-api-onboarding.md)。

---

## 贡献

欢迎提交 Issue 与 PR。涉及架构或接口的较大改动，请先开 Issue 讨论方案——本项目遵循 `DEV_SPEC.md` 中的架构设计，与之偏离的改动应在落地前对齐。

---

## 许可证

[MIT](LICENSE)
