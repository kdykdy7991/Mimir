# RAG Web UI 升级开发规格

> 状态：执行基线 v1.0
>
> 更新日期：2026-07-31
>
> 核心目标：完善可插拔 RAG 项目，并用独立、美观的 Web 前端替代 Streamlit 产品界面
>
> 执行分工：[TEAM_RESPONSIBILITIES.md](TEAM_RESPONSIBILITIES.md)

## 1. 项目定位

当前项目已经通过 Streamlit 实现 RAG MVP。本阶段不是建设多租户 SaaS，而是在保留现有能力的基础上完成两项升级：

1. 建设功能完整、组件可灵活替换、具备良好可观测性的 RAG 工程；
2. 建设视觉完善、交互清晰、适合作品展示和日常使用的独立 Web 前端。

RAG/MCP 的详细设计继续以 [`DEV_SPEC.md`](DEV_SPEC.md) 为准。本文只描述下一阶段的范围、Web API、Web UI 和验收标准。

## 2. 本阶段范围

### 2.1 必须完成

- 保持 LLM、Embedding、Loader、Splitter、Transform、Vector Store、Retriever、Reranker 和 Evaluator 可插拔；
- 完善 PDF 摄取、切分、图像处理、Embedding、Dense/Sparse 混合检索、融合、重排和引用返回；
- 统一查询、摄取和数据管理的应用服务，供 MCP、CLI、Streamlit 和 Web API 复用；
- 提供面向浏览器的 FastAPI HTTP API；
- 新建独立 Next.js + TypeScript Web 前端；
- 提供系统总览、知识库/集合、文档管理、摄取任务、查询体验和 Trace 页面；
- 支持上传进度、摄取阶段进度、错误提示、重试和删除确认；
- 展示检索片段、引用、关联图片及 Dense/Sparse/Rerank 诊断信息；
- 保留 Streamlit，作为内部调试和回归工具，不再承担产品 UI。

### 2.2 明确不做

- 用户注册、登录、会话和找回密码；
- Organization、Workspace、Membership、多租户和 RBAC；
- 计费、套餐、成员管理和复杂审计后台；
- 为“生产化”同时引入 PostgreSQL、Redis、对象存储、消息队列和多个检索数据库；
- 移动端 App、复杂工作流编排和会话型聊天系统；
- 与本阶段展示目标无关的大规模分布式部署能力。

如将来需要公网多人使用，再单独立项认证、多租户和生产基础设施，不提前污染当前架构。

## 3. 目标架构

```text
Browser
  │ REST（长任务首版可轮询，必要时使用 SSE）
  ▼
Next.js Web UI
  │
  ▼
FastAPI Adapter
  │
  ▼
Application Services
  ├── QueryService
  ├── IngestionService
  ├── DocumentService
  └── SystemService
  │
  ▼
Existing RAG Core / Pluggable Providers / Local Storage

MCP / CLI / Streamlit ─────► Application Services
```

依赖规则：

- Next.js 只调用 HTTP API，不了解 Python 内部类型和存储；
- FastAPI 只处理验证、DTO、错误转换和 HTTP 状态码；
- 应用服务负责用例编排，不能复制到不同入口；
- RAG 核心不依赖 Next.js、FastAPI 或 Streamlit；
- Provider 的切换继续由统一配置和 Factory 完成；
- 首版复用当前 Chroma、BM25、SQLite/JSONL 和本地图片存储，除非现有实现无法满足功能正确性。

## 4. 目录规划

```text
web/                         # 新 Next.js 产品前端
├── app/
├── components/
├── features/
├── lib/
├── types/                   # 由 OpenAPI 生成
└── tests/

src/
├── application/services/   # 多入口复用的应用服务
├── web_api/                # FastAPI 适配层
├── core/                   # 现有检索与响应能力
├── ingestion/              # 现有摄取能力
├── libs/                   # 可插拔 Provider
├── mcp_server/             # 改为调用应用服务
└── observability/
    └── dashboard/          # 保留的内部 Streamlit 工具
```

## 5. Web API 契约

API 前缀为 `/api/v1`。首版无需登录，部署到不可信网络时由反向代理或可选静态 API Key 提供边界保护。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/system/info` | 当前 Provider、配置摘要和能力状态 |
| GET | `/system/health` | 服务及依赖健康状态 |
| GET | `/collections` | 集合/知识库列表 |
| POST | `/collections` | 新建集合 |
| GET/DELETE | `/collections/{id}` | 集合详情和删除 |
| GET | `/collections/{id}/documents` | 文档列表 |
| POST | `/collections/{id}/documents` | 上传单个或批量文件并创建摄取任务 |
| GET/DELETE | `/documents/{id}` | 文档详情和协调删除 |
| GET | `/tasks/{id}` | 摄取状态、阶段、进度和错误 |
| POST | `/collections/{id}/queries` | 查询、检索和重排 |
| GET | `/queries/{id}/trace` | 查询链路诊断 |
| GET | `/ingestions/{id}/trace` | 摄取链路诊断 |

统一错误响应：

```json
{
  "error": {
    "code": "DOCUMENT_NOT_FOUND",
    "message": "Document does not exist",
    "request_id": "...",
    "details": {}
  }
}
```

约束：

- OpenAPI 是前后端唯一接口契约，前端类型由其自动生成；
- 列表使用一致的分页结构；
- 上传格式至少支持 `.pdf`、`.md` 和 `.markdown`，按扩展名与 MIME 类型双重校验；
- 单文件上传保持兼容；批量上传使用同一 `multipart/form-data` 请求中的
  `files[]`，所有文件进入同一目标 collection；
- 批量上传返回批次 ID 和逐文件任务结果（文件名、任务 ID、状态、错误），
  单个文件校验或摄取失败不得阻塞其他文件；
- 服务端限制单文件大小、单批文件数和批次总大小，拒绝项返回明确错误码；
- 查询限制 `top_k`、过滤器复杂度和返回大小；
- 图片通过受控 HTTP 地址返回，不在普通 JSON 长期传 Base64；
- 长任务首版允许轮询；只有轮询体验或负载不足时才引入 SSE；
- Provider 密钥不得通过 API 返回给浏览器。

## 6. Web 前端产品设计

### 6.1 页面范围

```text
/                         # 产品介绍或自动进入总览
/overview                 # 系统状态、Provider 和数据概览
/collections              # 集合列表与新建
/collections/[id]
├── /documents            # 文档、上传、删除和摄取状态
├── /playground           # RAG 查询体验
├── /traces               # 查询与摄取 Trace
└── /settings             # 当前集合和可公开配置
```

### 6.2 视觉与体验要求

- 建立统一颜色、字体、圆角、阴影、间距和交互状态，不沿用 Streamlit 默认外观；
- 桌面端优先并兼容平板，页面层级清晰；
- 总览突出当前 Provider、索引规模、最近摄取与查询状态；
- 所有数据页面具备 loading、empty、error 和 retry 状态；
- 上传支持拖拽、多文件选择、格式/大小/数量校验、总体与逐文件进度、
  重复文件提示、失败项单独重试；
- Query Playground 清晰区分查询、检索结果、引用、图片和诊断信息；
- Trace 使用时间线、阶段卡片或瀑布图，不直接堆叠原始 JSON；
- 删除等高风险操作必须确认并说明影响；
- Provider 密钥只显示“是否配置”，不显示值；
- 满足基础键盘操作、颜色对比和语义标签要求。

### 6.3 前端质量

- TypeScript strict；
- ESLint、格式化和类型检查；
- 关键组件测试；
- Playwright 覆盖 PDF/Markdown 单文件上传、混合格式批量上传、部分失败、
  摄取、查询、引用和删除主流程；
- OpenAPI 类型自动生成，禁止手工维护重复 DTO；
- 关键页面无明显布局跳动、控制台错误和未处理异常。

## 7. RAG 完整性验收

“完整”不等于堆积 Provider，以下能力必须真正形成闭环：

- 新 Provider 可通过实现统一接口、Factory 注册和配置完成接入，不修改业务流程；
- PDF 与 Markdown 从上传到索引均可完成，Markdown 标题、列表、代码块等
  结构在 canonical Markdown 和切分结果中正确保留；
- 同一批次可上传多个 PDF/Markdown 文件；每个文件独立去重、摄取、追踪和
  重试，部分失败时成功文件仍可查询；
- Dense 与 Sparse 检索可独立运行，也可通过融合组合；
- Reranker 可关闭、替换或失败降级；
- 查询结果包含稳定引用，可定位文档、页码/Chunk 和关联图片；
- 摄取和查询关键阶段产生结构化 Trace；
- 配置切换后 CLI、MCP、Streamlit 和 Web API 行为一致；
- 核心流程有单元、集成和 E2E 覆盖；
- README 能让新开发者完成安装、摄取、查询、启动 Web UI 和切换 Provider。

## 8. 实施阶段

### M1：边界和契约

> **状态：✅ 已完成（2026-07-31）** — FastAPI 骨架 + OpenAPI v0.1.1 冻结、应用服务层（Query/Ingestion/Document/System）+ 组合根、CLI/MCP/Streamlit 接线、Next.js 页面壳 + Mock、OpenAPI 类型生成、vitest 组件测试 + Playwright E2E 基座。

- 抽取 Query、Ingestion 和 Document 应用服务；
- CLI、MCP、Streamlit 统一调用应用服务；
- 建立 FastAPI 骨架和 OpenAPI 基线；
- 建立 Next.js 工程、设计 Token、页面壳和 Mock API。

验收：现有测试通过；Web 可使用 Mock 展示主要页面；前后端认可 API 契约。

### M2：核心数据闭环

> **状态：✅ 已完成（2026-07-31）** — Collection/Document/Upload/Task/Query/Image 真实端点，浏览器完成上传 → 摄取 → 查询 → 查看引用。

- 集合、文档、上传、摄取任务和查询 API；
- Web 完成总览、集合、文档和 Query Playground；
- 复用当前存储完成真实端到端接入。

验收：浏览器中可完成上传 → 摄取 → 查询 → 查看引用。

### M3：诊断和视觉完善

> **状态：✅ 已完成（2026-08-03，除 rerank）** — 批次 1 多集合路由、批次 2 异步查询 + Task/Trace 落 SQLite + `last_query_id`；`enable_rerank` 仍为 no-op（暂缓）。

- 查询/摄取 Trace API；
- Trace 时间线、Provider 状态和检索诊断界面；
- 全部空状态、错误、重试、删除确认和响应式处理；
- 组件、契约、集成和浏览器 E2E。

验收：主要页面达到展示标准，核心流程自动化测试通过。

### M4：文档和发布

> **状态：✅ 已完成（2026-08-03）** — 真实 `/system/health`、Makefile 统一启动、可选 Docker 部署、`scripts/benchmark.py` 性能基线、文档同步。

- 收口配置、示例数据、启动脚本和 README；
- 提供前后端本地启动及可选容器部署方式；
- 执行性能基线、依赖检查和发布回归。

验收：新环境可按 README 启动并完成完整演示流程。

### M5：Markdown 与批量摄取

> **状态：✅ 后端已完成（2026-08-06）；前端进度与逐文件 UI 待前端里程碑。**

- 新增 Markdown Loader，支持 `.md` / `.markdown`，以 UTF-8 为默认编码，
  保留标题、列表、表格、引用和 fenced code block；无法解码时返回明确错误；
- CLI、应用服务、Web API 统一通过 `LoaderFactory` / `LoaderRegistry` 按文件
  扩展名分派 PDF/Markdown，不在各入口重复判断格式；
- Web API 支持同一 collection 的多文件上传（`POST /collections/{id}/documents`
  的 `files[]`），建立批次与逐文件任务关系；
- 批量处理采用“逐文件独立提交”语义，不做全批次事务回滚；重复文件独立标记为
  `skipped`（任务终止态），失败文件可经同一端点单独重试；
- 前端展示总体进度和逐文件状态，并允许只重试失败文件（**见下方前端后续清单**）。

验收：一次选择至少 2 个混合格式文件（PDF + Markdown）后，成功文件均可在
文档列表中查看并被检索；重复文件被独立跳过；加入 1 个非法或损坏文件时，其余
文件仍成功，响应与界面可定位失败文件及原因。

**前端后续工作清单（本里程碑只交付后端，以下由前端接手）**：

1. `web/src/api/client.ts`：新增走批量端点的 `uploadDocuments(collectionId, files)`（FormData `files[]`），
   替代现有“并发单文件上传”的 `uploadDocuments`（`Promise.allSettled` 并行打单个 `file=`）；
2. `web/src/components/ui/status-badge.tsx`：补充 `skipped: { icon: …, label: "已跳过", tone: "neutral" }`；
3. `web/src/features/knowledge/documents-view.tsx`：任务轮询终止态由 `["failed","cancelled"]`
   扩展为 `["failed","cancelled","skipped"]`，否则重复文件的任务会无限轮询；
4. 批量上传 UI（拖拽多文件、逐文件状态、失败单独重试）；`.md` 文件必须发送
   `text/markdown` 或 `text/plain`（严格双重校验，`application/octet-stream` 会被 415 拒绝）。

## 9. 完成定义

功能只有同时满足以下条件才算完成：

- 成功、空状态、失败和重试路径均已处理；
- API 契约、实现和生成类型一致；
- 相关单元、契约、集成或 E2E 测试通过；
- 不泄露密钥、内部路径或敏感配置；
- 页面视觉和交互达到统一标准；
- 文档与当前实现一致；
- 不破坏 MCP、CLI 和 Streamlit 已有能力。

## 10. 后续可选方向

以下内容只有出现明确需求时再立项：

- 最终答案生成、流式回答和会话历史；
- Office、网页等更多 Loader；
- Ragas/DeepEval 评估面板和测试集管理；
- API Key、OIDC 登录、多用户、多租户和 RBAC；
- 队列、对象存储、PostgreSQL、Qdrant/OpenSearch 和分布式部署；
- 告警、SLO、备份恢复和灰度发布。
