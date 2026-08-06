# 双人开发职责与协作约定

> 状态：执行基线 v2.1
>
> 适用范围：[RAG Web UI 升级开发规格](PRODUCTION_WEB_DEV_SPEC.md)
>
> 角色：A（Web 前端负责人）、B（RAG 与 API 负责人）

## 1. 总体划分

| 负责人 | 核心目标 | 独占范围 |
|---|---|---|
| A | 把现有 Streamlit MVP 升级为美观、易用的独立 Web 产品界面 | `web/**` |
| B | 完善可插拔 RAG，并提供稳定的应用服务和 HTTP API | 除 `web/**` 外的代码与基础配置 |

每项工作只有一个直接负责人（DRI）。Streamlit 由 B 保留为内部调试工具，A 不负责美化或继续扩展 Streamlit。

## 2. A：Web 前端负责人

A 负责：

- Next.js + TypeScript strict 工程、路由、布局和构建；
- 视觉设计系统、组件库、响应式和基础无障碍；
- 总览、集合、文档、摄取任务、Query Playground 和 Trace 页面；
- OpenAPI 客户端生成及浏览器端 API 封装；
- 上传、任务轮询/SSE、查询、引用、图片和诊断交互；
- loading、empty、error、retry 和删除确认；
- 组件测试、Playwright E2E、前端使用与开发文档。

A 不负责：

- Python RAG 逻辑、FastAPI Schema、数据库和存储实现；
- Provider 接入、MCP、CLI 和 Streamlit；
- 登录、用户、组织、多租户和 RBAC；
- 在前端保存或展示 Provider 密钥。

## 3. B：RAG 与 API 负责人

B 负责：

- Loader、Splitter、Transform、Embedding、Vector Store、Retriever、Fusion、Reranker、Evaluator 的接口、Factory、实现和配置；
- 摄取、混合检索、重排、引用、多模态内容和 Trace 的完整闭环；
- QueryService、IngestionService、DocumentService 和 SystemService；
- FastAPI、OpenAPI、错误码、分页、上传、任务状态和图片访问接口；
- 当前 Chroma、BM25、SQLite/JSONL、本地图片存储的正确性和协调删除；
- MCP、CLI 和 Streamlit 统一调用应用服务；
- 后端单元、契约、集成、E2E 和性能基线；
- API、配置、Provider 扩展和本地运行文档；
- 为 A 提供稳定 API、Mock 示例、示例文件和可复现测试数据。

B 不负责：

- `web/**` 页面、样式、浏览器状态和前端组件；
- 为某个页面复制一套业务逻辑或提供未进入 OpenAPI 的私有接口；
- 当前阶段未立项的认证、多租户和分布式基础设施。

## 4. 所有权和交接

| 资产 | DRI | 另一方职责 |
|---|---|---|
| `web/**` | A | B 评审接口和密钥风险 |
| `src/**`、`scripts/**`、`config/**` | B | A 按需反馈消费需求 |
| OpenAPI 源 Schema | B | A 评审是否足够支持页面 |
| OpenAPI 生成类型 | A | B 保证源契约稳定 |
| 后端测试 | B | A 提供关键用户场景 |
| 前端组件和 Playwright | A | B 提供可用环境和数据 |
| Streamlit Dashboard | B | A 无交付责任 |
| UI 文档和截图 | A | B 评审事实准确性 |
| API、RAG 和配置文档 | B | A 评审可理解性 |

接口交接时，B 必须给出请求/响应 Schema、成功和失败示例、稳定错误码、分页/任务状态约定及契约测试。A 必须确认页面需要的所有状态都能表达。

接口变更顺序：先修改并评审 OpenAPI，再由 B 实现，最后由 A 重新生成类型并接入。生成类型禁止手改。

### 4.1 B 向 A 提供的正式交付物

A 的工程、视觉系统、布局、页面壳、通用状态和测试基座不依赖后端，可以立即开发。B 不需要等真实 API 完成，但必须按以下三个检查点向 A 交付结果。

#### 检查点一：v0.1 Mock 契约

B 应在 2 个工作日内提交：

```text
docs/openapi/openapi.v0.1.json
docs/openapi/examples/
docs/web-api-onboarding.md
```

`openapi.v0.1.json` 必须至少覆盖：

- `/api/v1/system/info` 和 `/api/v1/system/health`；
- Collection 的 list/create/get/delete；
- Document 的 list/upload/get/delete；
- Task 的 get；
- Query 的 create；
- Query Trace 和 Ingestion Trace 的 get；
- 受控图片访问地址及响应类型。

OpenAPI 必须完整描述请求、响应、HTTP 状态码、UUID、UTC 时间、可空字段、分页、任务状态/阶段/进度、Citation、图片和统一错误信封。Schema 可以先于真实实现完成，但未经 A、B 双方确认不得删除字段、改变字段类型或改变枚举语义。

`examples/` 必须提供能直接建立 MSW Mock 的代表性数据：

- 列表的非空和空状态；
- Task 的 pending、running、succeeded 和 failed；
- Query 的正常结果、空结果和检索降级；
- Upload 的成功、格式不支持和文件过大；
- 通用 404、409、422 和 500 错误。

`web-api-onboarding.md` 必须说明：

- 后端启动命令、端口和 `/api/v1` Base URL；
- OpenAPI 在线地址和快照生成方式；
- 本地 CORS 或 Next.js 代理约定；
- 前端环境变量名称；
- 请求超时和 `request_id` 传递约定；
- 分页默认值/上限及游标规则；
- 上传协议、格式和大小限制；
- Task 轮询间隔、终态和错误行为；
- 图片 URL 解析规则；
- 当前无登录、无用户、无多租户和无 RBAC。

B 完成上述三个路径后必须通知 A，并附带准确文件路径和对应提交/变更说明。A 收到后负责生成 TypeScript 类型、建立 MSW handlers 并完成第一次契约评审。CI 快照测试尚未完成不阻塞 A 开始 Mock 接入。

#### 检查点二：可运行的真实 API

B 每完成一个可运行的 API 功能组，应通知 A，而不必等待整个 M2 完成。每次交付必须包含：

- 已实现端点清单；
- 本地启动和初始化命令；
- 可重复创建的测试数据或 fixture；
- 对应契约测试结果；
- 已知限制和暂未实现行为；
- 可用于排查问题的服务端日志或 `request_id`。

真实 API 按以下顺序交付和接入：

1. System API → A 接入总览；
2. Collection API → A 接入集合页面；
3. Document、Upload、Task API → A 接入文档与摄取进度；
4. Query、Citation、Image API → A 接入 Playground；
5. Query/Ingestion Trace API → A 接入 Trace 页面。

端点只有在实现与 OpenAPI 一致、契约测试通过且测试数据可复现时，才视为可供 A 联调。A 不以未实现的后端行为作为前端完成标准。

#### 检查点三：v1 契约冻结

M1 结束时，B 必须提供：

- 最新 OpenAPI 快照；
- FastAPI 实际输出与快照一致性测试；
- 从空环境启动 API 的说明；
- 稳定错误码清单；
- v0.1 至 v1 的变更记录。

A 重新生成类型、运行 Mock/组件测试并确认页面状态均可表达后，A、B 双方完成 v1 契约确认。未经双方确认，不得把 v1 标记为冻结。

### 4.2 不应阻塞前端的事项

- B 的 Schema、settings、错误码、示例、文档和快照测试阶段可以连续执行，不必每完成一个小阶段都等待 A；
- 只有会改变领域语义、OpenAPI 字段、类型、枚举或错误行为的问题才需要暂停并请求 A 评审；
- M2/M3 的真实实现、性能测试、SSE、部署和发布工作不阻塞 A 开展页面壳和 Mock 开发；
- SSE 不是首版前置条件，Task 首版使用轮询；是否增加 SSE 在真实轮询流程完成后另行决定；
- A 不要求 B 为前端便利扭曲后端领域模型，但 B 必须通过稳定契约表达真实领域行为。

## 5. 并行任务计划

### M1：边界和骨架

> **状态：✅ 已完成（2026-07-31）**
>
> - FastAPI 骨架 + OpenAPI v0.1.1 契约冻结（11 paths / 27 schemas）、错误信封、分页/时间/枚举规范；
> - 应用服务层 `src/application/services/`（Query / Ingestion / Document / System）+ 组合根 `src/application/composition.py`；
> - CLI（query / ingest）、MCP `query_knowledge_hub`、Streamlit dashboard 已接线到应用服务；
> - `web/` 页面壳（总览 / 集合 / 文档 / Playground / Trace）+ 设计 Token + Mock 数据；
> - OpenAPI 类型生成（`npm run gen:types` → `src/types/api.ts`）；
> - 组件测试基座（vitest，10 用例）与 Playwright E2E 基座（`tests/e2e/smoke.spec.ts`）。

**A**

- 初始化 `web/`、质量脚本和设计 Token；
- 完成导航、总览、集合、文档和 Playground 页面壳；
- 建立 OpenAPI 生成与 Mock API；
- 建立组件测试和 Playwright 基座。

**B**

- 抽取 Query、Ingestion、Document 应用服务；
- 让 MCP、CLI、Streamlit 复用应用服务；
- 建立 FastAPI 和 `/api/v1` OpenAPI 基线；
- 提供 system、collection、document、task、query 的 Mock 示例。

出口：现有测试通过，A 可用 Mock 完整走通主要页面，双方冻结第一版契约。

### M2：真实闭环

**A**

- 接入真实集合、文档、上传、任务和查询 API；
- 完成上传进度、任务状态、错误重试；
- 展示检索片段、引用和图片。

**B**

- 完成对应 API 和契约测试；
- 确保上传、摄取、混合检索、重排、引用和删除闭环；
- 提供稳定示例数据和诊断日志。

> **M2 进度**：
> - ✅ 批次 1（2026-07-31）：System info/health、Collections CRUD、Documents detail/delete 真实端点已接线应用服务；`create_app(services=...)` 注入 + 懒构建；文档/集合稳定 UUID 派生；契约快照刷新（结构未变，仅描述更新）。
> - ✅ 批次 2（2026-07-31）：Upload → Task（同步执行 + 状态机 + 轮询）。
> - ✅ 批次 3（2026-07-31）：Query / Trace / Images 真实端点 + 契约 v0.2 冻结。

> **M3 进度（2026-08-03）**：
> - ✅ 批次 1：非 default 集合多索引（EngineCache + MultiCollectionVectorStore），查询/摄取/文档/删除按集合路由；`resolve_document` 扫所有集合。
> - ✅ 批次 2：异步查询（`POST .../queries/async` + `GET /queries/{id}/result`）；Task/Trace/异步结果落 SQLite（`data/db/web_api.db`，重启后 task 可查）；`DocumentDetail.last_query_id`。
> - ⏳ 剩余：**rerank 真实接入**（`enable_rerank` 仍 no-op，已确认暂不做）。

> **M4 进度（2026-08-03）**：
> - ✅ 真实 `/system/health` + Provider `ready`（embedding/chroma/sqlite/bm25 真实探测，5s TTL 缓存）。
> - ✅ 统一启动（Makefile：`make dev` / `api` / `web` / `test` / `benchmark`）+ 可选 Docker 部署（Dockerfile + compose）。
> - ✅ 性能基线 `scripts/benchmark.py`（本机实测 p95 ≈ 25ms，预算 2s）。
> - ✅ 文档同步（README / onboarding / 职责进度 / PRODUCTION spec 状态标记）。

出口：浏览器可完成上传 → 摄取 → 查询 → 引用 → 删除。

### M3：展示质量

**A**

- 完成 Trace 时间线、检索诊断和 Provider 总览；
- 收口视觉、响应式、空状态、错误状态和确认交互；
- 完成组件测试和关键 Playwright。

**B**

- 完成 Query/Ingestion Trace 与系统信息 API；
- 验证 Provider 切换和失败降级；
- 完成后端集成回归和性能基线。

出口：功能完整、视觉统一、核心自动化测试通过，达到作品演示标准。

### M4：发布收口

**A**：完善前端 README、截图、构建和演示流程。

**B**：完善总 README、配置、启动脚本、API/Provider 扩展文档和可选容器运行。

出口：新环境能按 README 启动，并完成完整演示流程。

## 6. 缺陷与验收归属

- 页面渲染、样式、交互、浏览器状态和前端性能问题归 A；
- API、RAG、数据、Provider、任务、Trace 和运行问题归 B；
- 契约缺失或实现偏离 OpenAPI 归 B；契约正确但前端消费错误归 A；
- Playwright 由 A 编写，B 保证 API 环境、示例数据和服务端日志可用；
- 跨端问题由发现者提供最小复现，双方分别检查浏览器请求和 API Trace 后确定 DRI。

PR 必须包含范围、验证结果、必要截图/API 示例和风险。跨边界契约变更必须双方批准。

## 7. 明确不分派的任务

当前不得以“以后可能需要”为理由分派以下任务：

- 登录、用户中心、组织和成员管理；
- 多租户、RBAC、审计后台和计费；
- Redis 队列、S3、PostgreSQL 或 Kubernetes 迁移；
- 与当前 RAG 完整性和 Web 展示无关的企业平台能力。

新增上述范围必须由项目负责人单独批准并更新开发规格。
