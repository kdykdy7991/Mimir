# WeKnora 可用性优化借鉴：前端实施状态

> 任务书：`docs/plan-2026-09-11-weknora-ux-frontend-tasks.md`
> 分支：`feature/weknora-inspired-optimizations`
> 开始日期：2026-09-11

## 工作区保护

开始前确认并保留了用户已有的前端改动：

- 知识库卡片整卡点击与强化交互；
- 文档长文件名截断；
- MCP Server 接入信息、配置复制和对应测试；
- `web/.env.example`、`web/README.md` 的 MCP URL 说明。

本计划不覆盖或回退上述改动。

## F0 类型、路由状态和测试基座

### F0.1 更新 API Client 与契约类型

状态：完成（已消费 2026-09-11 后端 OpenAPI）

实现：

- 新增 `web/src/types/ux-contracts.ts`，严格按已冻结后端任务书定义 Chunk、标签、文件夹、批量操作、Trace 列表、MCP 状态和连通测试的过渡类型。
- `web/src/types/index.ts` 统一导出上述类型。
- `web/src/api/client.ts` 新增所有对应的类型安全 API 方法。
- 查询参数使用统一序列化，数组按重复 query 参数传递。
- 所有读取方法支持 `AbortSignal`。
- MCP 连通测试 Key 只进入 POST JSON body，不进入 URL。

说明：后端 B0–B4 落地后已运行 `npm run gen:types`；生成契约已包含新增端点。UI 边界的少量别名仍集中在 `ux-contracts.ts`，API Client 已按真实 envelope、`text_preview` 和 `page_info` 做统一适配。

测试：

```text
npm test -- --run src/api/client.test.ts src/lib/filter-url-state.test.ts
17 passed

npm run typecheck
passed

npm run lint -- src/api/client.ts src/api/client.test.ts \
  src/lib/filter-url-state.ts src/lib/filter-url-state.test.ts \
  src/types/index.ts src/types/ux-contracts.ts
passed
```

### F0.2 建立页面查询状态工具

状态：完成

实现：

- 新增 `web/src/lib/filter-url-state.ts`。
- 支持默认值省略、键稳定排序、多值稳定排序、正整数校验、枚举回退、重复值归一化。
- 该工具供后续 Chunk、文档和 Trace 筛选共同使用，避免各页面复制 URL 解析逻辑。

测试覆盖：中文查询、多值参数、浏览器刷新/回退可恢复形式、非法页码、非法每页数量和非法枚举。

## 后端契约接入

后端 B0–B4 的 Chunk、标签、文件夹、批量操作、Trace、task retry/cancel、MCP 状态与测试端点均已接入真实 API Client。

## F1 Chunk 检查与原文定位

### F1.3 Chunk 详情抽屉

状态：完成并接入文档详情

实现：

- 新增 `web/src/features/knowledge/chunk-detail-drawer.tsx`。
- 桌面为右侧抽屉，窄屏为全屏 Dialog。
- 展示完整正文、章节、页码、类型、字符数和 Chunk ID。
- 支持复制正文、复制 ID、上一段/下一段和原文定位回调。
- 覆盖加载、空态、可重试错误和复制失败。
- 使用原生 modal dialog 提供 Escape 与焦点限制，关闭后恢复触发元素焦点。
- 正文以纯文本渲染，不注入 HTML。

测试：`web/src/features/knowledge/chunk-detail-drawer.test.tsx` 覆盖内容、首尾导航、复制、错误重试、关闭和焦点恢复。

接入状态：B1.1 单 Chunk 详情端点已接入。

### F1.1 / F1.2 / F1.4 Chunk 页面接入

状态：完成（按冻结后端契约实现）

实现：

- 文档详情改用公共 Chunk 分页接口，支持 20/50/100 每页、总数和前后翻页。
- 支持 300ms 正文搜索、内容类型和原文页码过滤，以及一键清除。
- 新请求通过 AbortSignal 取消旧请求，避免陈旧响应覆盖。
- 点击结果读取单 Chunk 详情并打开 F1.3 抽屉。
- 原文定位支持 PDF `#page=N`；section/none 明确降级，不伪造精确定位。
- 测试 Mock 已与冻结契约同步。

## F3 可操作 Trace 时间线

### F3.1 Trace 状态视觉规范

状态：完成

实现：

- `trace-presentation.ts` 建立 pending/running/success/warning/failed/skipped/canceled 七态规范。
- 对 processing/succeeded/ready/ok/cancelled 等旧状态提供单向规范化。
- 跳过阶段优先展示后端提供的具体 `skip_reason`。
- `StatusBadge` 补齐 success/warning/skipped/canceled，并为旋转图标增加 reduced-motion 降级。
- 状态始终同时使用图标、文案和颜色表达。

## F4 MCP 状态与连通测试

### F4.2 连通测试步骤组件

状态：完成（已接入一次性 Secret Dialog）

实现：

- 新增 `mcp-connection-diagnostics.tsx`。
- 展示 connect、initialize、tools/list、list_collections 四个协议阶段。
- 每步支持 pending/running/success/failed/skipped、耗时和安全统计。
- 展示后端分类后的安全错误说明、建议动作和 Request ID。
- 组件只接收测试结果，不接收、读取或保存 MCP Key。
- 运行态动画尊重 reduced-motion。

### F4.1 MCP Server 状态卡

状态：完成（按冻结后端契约实现）

- 展示 MCP 在线、降级、离线、配置错误以及上游 API 独立状态。
- 展示探测耗时、检查时间和手动刷新。
- 状态请求失败不阻断 Key 管理。

### F4.3 / F4.4 创建与轮换 Key 后立即测试

状态：完成（按冻结后端契约实现）

- 一次性 Secret Dialog 中提供“测试连接”。
- 完整 Key 仅在点击测试时通过 POST body 发送。
- 展示 F4.2 四阶段结果；关闭弹窗后 React Secret state 被销毁。
- 请求失败转换为安全诊断，不在 URL、持久化或错误文案中输出 Key。

## F2 文档组织与批量操作

状态：完成

- 知识库详情新增标签与文件夹管理面板，支持创建及安全删除。
- 文档行展示最多两个标签及 `+N`，长名称保留 tooltip。
- 文档搜索改为服务端筛选，不再只过滤当前页。
- 支持当前页单选/全选，选择后提供批量加标签、移动、重新解析和删除。
- 批量响应按逐项结果统计，部分失败会显示失败原因。
- 翻页、搜索和批量完成后清空选择，避免对不可见条目误操作。

进展补充（2026-09-11）：单篇标签搜索/多选/全量替换、标签改色、目录移动、状态/格式/日期/排序组合筛选、批量标签 add/remove/replace 已接入；多标签使用重复 `tag_id` 保持 AND 语义并同步 URL；活动筛选可逐项清除；当前页全选支持半选态；批量响应展示成功统计和逐项失败代码/原因。

## F3.2–F3.6 Trace 实时与历史

状态：完成

- Ingestion 运行态自动轮询，后台标签页降低频率，终态停止。
- 后端 `retryable/cancelable` 驱动重试和取消入口。
- Trace Explorer 新增类型、状态、文档名/Request ID 搜索的历史列表，并可直接打开详情。
- 历史列表已接入游标“加载更多”；retry/cancel 已增加确认和重复提交保护。

进展补充（2026-09-11）：已接入 collection、起止日期、URL 状态、游标加载更多；知识库筛选使用名称提示但提交稳定 ID；阶段详情只展示契约允许的计数、attempt、skip reason、error code/summary，不输出任意 details JSON；新增 Trace 历史选择与安全展示组件测试。

## 最终门禁

```text
npm run typecheck: passed
npm test -- --run: 19 files / 70 tests passed
npm run lint: passed
npm run build: passed (Next.js production build, 9 routes)
git diff --check: passed
```
