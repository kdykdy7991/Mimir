# SKDY RAG Web

独立 Next.js + TypeScript Web 控制台，用于管理和调试 SKDY RAG MCP Server。

- 使用冻结的 `v0.2` OpenAPI 契约（`docs/openapi/openapi.v0.2.json`）开发，System、知识库、文档、摄取、检索、图片和 Trace 均已接入真实 API；
- 前端类型由 OpenAPI 自动生成，禁止手工维护重复 DTO；
- MSW fixtures 支持组件测试，Playwright fixtures 支持不依赖 Provider 的浏览器流程。

该 Web 不是会话型聊天客户端。Playground 用于检查 MCP Server 返回的检索上下文、Citation、图片、分数、降级与 Trace；最终自然语言回答、工具选择和会话管理由调用 MCP Tool 的 Agent 负责。

## 环境要求

- Node.js ≥ 20
- npm

## 安装

```bash
npm install
```

## 开发

```bash
cp .env.example .env.local
npm run dev
# http://localhost:3000
```

`NEXT_PUBLIC_API_BASE_URL` 是 FastAPI 的 Origin，默认值为
`http://127.0.0.1:8766`；路径中的 `/api/v1` 由统一 API Client 管理。

## 脚本

| 命令 | 说明 |
|------|------|
| `npm run dev` | 开发服务器 |
| `npm run build` | 生产构建 |
| `npm run start` | 启动生产构建（需先 build） |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | vitest 组件/单元测试 |
| `npm run gen:types` | 从 OpenAPI 契约生成 `src/types/api.ts` |
| `npm run test:e2e` | Playwright 浏览器 E2E（需先安装浏览器，见下） |

## 生成类型

```bash
npm run gen:types
```

从 `../docs/openapi/openapi.v0.2.json` 生成 `src/types/api.ts`，再由 `src/types/index.ts` 统一导出。契约变更后需重新生成并提交。

业务代码通过 `src/api/` 调用接口，不直接使用裸 `fetch`。Client 统一处理
60 秒超时、`X-Request-ID`、错误信封、分页游标、上传表单和图片 URL。

## E2E 测试（Playwright）

首次运行前安装浏览器（约 200MB）：

```bash
npx playwright install chromium
```

然后运行：

```bash
npm run test:e2e
```

该命令会先在 3100 端口构建并启动生产服务器，再执行 `tests/e2e/` 下的冒烟用例。

## Mock 与测试

`src/test/mocks/` 提供与 v0.2 契约一致的 MSW handlers 和 fixtures，用于组件测试；运行中的页面默认调用 `NEXT_PUBLIC_API_BASE_URL` 指向的真实 FastAPI，不会自动启用演示数据。

当前 Web 控制台覆盖：

- System 与 Provider 配置观察；
- 知识库、文档、上传、Task 轮询与删除；
- Hybrid / Dense / Sparse 检索调试；
- Citation、受控图片、降级原因和 Query/Ingestion Trace。
