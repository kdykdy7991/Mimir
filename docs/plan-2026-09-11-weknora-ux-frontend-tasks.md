# WeKnora 可用性优化借鉴：前端开发任务书

> 状态：待实施
> 文档日期：2026-09-11
> 目标分支：`feature/weknora-inspired-optimizations`
> 前端目录：`/home/hello/workspace/SKDY-RAG-SERVER/web`
> 配套后端任务书：`docs/plan-2026-09-11-weknora-ux-backend-tasks.md`

## 1. 目标与原则

本计划将四项后台能力转化为可直接使用的管理台体验：

1. 在文档详情中检查 Chunk 全文并定位原文；
2. 用标签、文件夹、筛选和批量操作管理文档；
3. 将处理 Trace 升级为实时、可诊断、可操作的时间线；
4. 在 MCP Key 页面展示服务状态并完成一次性连通测试。

每个 F 编号都是独立开发任务，应独立测试和提交。不得以“重做知识库页面”为一个任务打包实施。

## 2. 技术与交互约束

- 当前前端为 Next.js；动手前必须阅读 `web/AGENTS.md` 及本地 `node_modules/next/dist/docs/` 中与所改路由、数据获取相关的文档。
- 延续现有设计 token、`glass-surface`、Button、状态组件和中文信息架构，不引入第二套组件库。
- API 类型来自 OpenAPI 时必须重新生成，不长期维护重复手写类型；MCP 管理临时手写类型应随契约生成逐步收敛。
- 列表筛选同步 URL，刷新和前进/后退后状态不丢失。
- 所有异步交互必须有 loading、empty、error、success 四态。
- 所有图标按钮必须有可访问名称和 tooltip；Dialog/Drawer 支持 Escape、焦点圈定和关闭后焦点恢复。
- 不能只依靠颜色表达状态。
- 移动端不得依赖 hover；表格必须有窄屏替代布局或横向滚动策略。
- 完整 MCP Client Key 只存在创建/轮换弹窗的内存状态中，禁止 localStorage/sessionStorage/URL/错误上报。
- 前端不得访问 `/internal/mcp/v1`，不得读取 `MCP_INTERNAL_API_KEY`。

## 3. 当前页面基线

| 页面 | 当前能力 | 本轮目标 |
| --- | --- | --- |
| `/collections/[id]` | 文档列表、上传、删除 | 文件夹树、标签、组合筛选、批量操作 |
| `/documents/[id]` | 原文件预览、处理信息、Trace 摘要、Chunk 元数据表 | Chunk 全文抽屉、搜索过滤、分页、原文定位 |
| `/traces` | 查询/摄取 Trace 查看 | 过滤列表、实时状态、阶段详情、retry/cancel |
| `/mcp-keys` | Server 地址、配置复制、Key CRUD | 服务状态、连通测试和诊断 |

关键实现位置：

- `web/src/features/knowledge/collection-detail-view.tsx`
- `web/src/features/knowledge/document-detail-view.tsx`
- `web/src/features/knowledge/document-table.tsx`
- `web/src/features/traces/trace-explorer.tsx`
- `web/src/features/system/mcp-keys-view.tsx`
- `web/src/api/client.ts`
- `web/src/types/`

## 4. 前后端协作规则

- F0 先消费后端 B0 冻结的 OpenAPI；接口尚未完成时使用与契约完全一致的 fixture/MSW mock，不在组件里兼容多个猜测字段。
- 每个请求通过 `apiClient`，组件不得散落手写 `/api/v1` fetch；原文件 blob 预览沿用已有受控方法。
- 后端返回 `retryable/cancelable/source_locator/error.code`，前端只据契约展示，不自行推断权限或任务状态。
- 若验收发现契约缺口，先修改后端任务书与 OpenAPI，再修改实现。

## 5. 原子开发任务

### F0：类型、路由状态和测试基座

#### F0.1 更新 API Client 与生成类型

产出：

- 为新增 Chunk、标签、文件夹、批量、Trace、MCP 状态/测试端点提供类型安全方法。
- 所有可取消读取接收 `AbortSignal`。
- 对搜索参数统一用 `URLSearchParams`，数组参数按契约重复传递。

测试：每个方法验证 method、URL 编码、query、body 和 signal。

提交主题：`feat(web-api): add knowledge operations clients`

#### F0.2 建立页面查询状态工具

提供轻量工具处理：

- URL → 筛选状态；
- 筛选状态 → URL；
- 默认值省略；
- 多标签稳定排序；
- 无效参数安全回退。

不得在文档列表、Trace 列表分别复制解析逻辑。

测试：刷新、浏览器回退、多值参数、中文搜索、无效页码。

提交主题：`feat(web): standardize filter url state`

---

### F1：Chunk 检查与原文定位

#### F1.1 Chunk 列表改为服务端分页

修改文档详情的“文档片段”区域：

- 首次只拉第一页；
- 显示 `total`，不再用当前数组长度冒充总数；
- 上一页/下一页和页码；
- 每页 20/50/100；
- 切页保留滚动区域位置；
- 新请求取消旧请求，避免快速切换时旧响应覆盖新响应。

验收：0、1、恰好一页、多页、最后空页回退、请求失败均有明确状态。

提交主题：`feat(web): paginate document chunks`

#### F1.2 Chunk 搜索和类型筛选栏

控件：

- 正文搜索；
- 类型：全部、正文、表格、图片 OCR、图片描述；
- 可选页码过滤；
- 清除筛选；
- 结果数。

行为：

- 搜索输入 300ms debounce；
- 输入法 composition 期间不请求；
- 条件变化回到第一页；
- 状态进入 URL；
- 空结果说明当前筛选条件。

提交主题：`feat(web): filter document chunks`

#### F1.3 Chunk 详情抽屉

新增独立组件，例如 `chunk-detail-drawer.tsx`：

- 点击整行打开；
- 显示完整正文、章节、页码、类型、字符数和 ID；
- 复制正文、复制 Chunk ID；
- 上一条/下一条；
- 加载骨架、错误重试；
- Escape 关闭、焦点圈定、关闭后回到原行；
- 正文使用可换行、可选择文本，不使用危险 HTML 注入。

移动端使用全屏 Sheet，桌面使用右侧 Drawer。

测试：打开/关闭、键盘、首尾导航、复制成功/失败、慢请求和错误。

提交主题：`feat(web): inspect full chunk content`

#### F1.4 原文定位交互

在抽屉中增加“在原文中查看”：

- `pdf_page`：更新 PDF iframe URL 的 `#page=N`，并滚动到预览区；
- `image`：滚动到图片预览；
- `section`：第一版打开原文件预览并显示“已定位到章节信息”，无法可靠 DOM 定位时不伪装精确定位；
- `none`：显示“该格式暂无精确定位”，仍提供打开原文件。

要求：定位状态可见，返回 Chunk 抽屉后上下文不丢失。

提交主题：`feat(web): navigate chunks to source previews`

F1 Phase Gate：组件测试、文档详情回归、PDF 页码定位浏览器验收、移动端抽屉验收通过。

---

### F2：标签、文件夹、筛选与批量操作

#### F2.1 文档标签展示

在文档行显示最多 2 个标签，超出显示 `+N`；名称截断但 tooltip 展示完整值。颜色只使用后端允许的 token。

提交主题：`feat(web): display document tags`

#### F2.2 单篇文档标签编辑

在文档行操作或详情页打开标签选择器：

- 搜索标签；
- 多选；
- 保存时全量替换；
- 无标签空态；
- 保存失败保留用户选择并允许重试。

不在本任务中实现标签 CRUD。

提交主题：`feat(web): edit document tag assignments`

#### F2.3 标签管理弹窗

实现：创建、重命名、改颜色、删除；删除前显示受影响文档数。名称冲突和长度错误显示字段级提示。

提交主题：`feat(web): manage collection tags`

#### F2.4 文件夹树只读导航

先实现导航，不含编辑：

- 左侧树和根目录“全部文档”；
- 展开/折叠；
- 当前目录强化选中；
- 直接文档数量；
- URL 保存 `folder_id`；
- 空目录正常显示。

展开状态使用 collection 维度的本地 UI 状态；只保存 folder ID，不保存敏感数据。

提交主题：`feat(web): navigate document folders`

#### F2.5 文件夹管理交互

在 F2.4 基础上增加：

- 新建子目录；
- 重命名；
- 删除空目录；
- 移动目录；
- 最大深度和冲突错误提示。

禁止乐观删除；移动可以乐观展示，但失败必须回滚。

提交主题：`feat(web): manage document folders`

#### F2.6 文档组合筛选栏

增加：

- 文件名搜索；
- 状态；
- 文件格式；
- 多标签；
- 更新时间范围；
- 排序；
- 条件 Chip 与一键清除。

文件夹由 F2.4 提供，所有条件共同进入一次服务端查询。移动端使用可展开筛选面板。

提交主题：`feat(web): filter collection documents`

#### F2.7 文档选择模型

只实现选择行为：

- 单选；
- 全选当前页；
- 半选状态；
- 翻页后选择策略明确，第一版只保留当前页选择；
- 筛选变化清空选择并给出可预期反馈；
- 键盘可操作。

不在本任务调用批量 API。

提交主题：`feat(web): select collection documents`

#### F2.8 批量标签操作栏

选择后出现浮动操作栏，支持添加/移除/替换标签。完成后展示成功/失败数量和失败项，成功项刷新。

提交主题：`feat(web): batch tag documents`

#### F2.9 批量移动操作栏

提供目标文件夹选择和移动到根；显示面包屑路径，防止选错同名目录；处理部分成功。

提交主题：`feat(web): batch move documents`

#### F2.10 批量重新解析

单独的确认弹窗说明：

- 将产生新处理任务；
- 原文件必须仍可用；
- 运行中的文档可能被拒绝；
- 最多选择数量。

提交后跳转或提供入口查看新任务 Trace。

提交主题：`feat(web): batch reprocess documents`

#### F2.11 批量删除

危险操作独立实现：

- 明确显示选中数量；
- 要求二次确认；
- 请求期间禁止重复提交；
- 逐项展示失败原因；
- 删除成功项从列表移除；
- 不把部分失败显示成整体成功。

提交主题：`feat(web): batch delete documents`

F2 Phase Gate：标签、文件夹、筛选 URL、选择、四类批量操作组件测试和 collection 详情浏览器验收通过。

---

### F3：可操作 Trace 时间线

#### F3.1 Trace 状态视觉规范

建立单一 presentation mapping：

| 状态 | 图标/文案要求 |
| --- | --- |
| pending | 时钟 + 等待中 |
| running | 动态但尊重 reduced-motion + 处理中 |
| success | 对勾 + 已完成 |
| warning | 警告图标 + 已完成但有警告 |
| failed | 错误图标 + 失败 |
| skipped | 跳过图标 + 跳过原因 |
| canceled | 停止图标 + 已取消 |

颜色、图标、文本三者共同表达状态；替换散落在页面中的临时判断。

提交主题：`refactor(web): unify trace status presentation`

#### F3.2 文档详情实时处理时间线

升级已有“处理流程”：

- 展示顶层状态和当前阶段；
- 展示阶段输入/输出数量、耗时、attempt；
- 阶段可展开查看安全详情；
- running 时 2 秒轮询，后台标签页降频，终态立即停止；
- 跳过和警告不冒充成功；
- 页面卸载取消请求。

提交主题：`feat(web): render live ingestion timelines`

#### F3.3 Trace 阶段错误详情

独立错误面板：

- 错误码；
- 用户安全摘要；
- Request ID/Trace ID 复制；
- 建议动作；
- 不渲染完整堆栈。

提交主题：`feat(web): explain trace failures safely`

#### F3.4 重试操作

仅当 `retryable=true` 显示：

- 二次确认；
- 防重复点击；
- 成功后跳到新 Trace 或更新链接；
- 明确展示“这是第 N 次尝试”；
- 原 Trace 保留。

提交主题：`feat(web): retry failed ingestion traces`

#### F3.5 取消操作

仅当 `cancelable=true` 显示：

- 说明取消不会回滚已完成阶段；
- 确认后显示“正在请求取消”；
- 轮询直到 canceled 或其他终态；
- 冲突错误解释任务可能刚刚完成。

提交主题：`feat(web): cancel active ingestion traces`

#### F3.6 Trace Explorer 筛选和分页

增强 `/traces`：

- 类型、状态、知识库、时间范围；
- 文档名/Request ID 搜索；
- 失败优先快捷筛选；
- URL 状态；
- 游标加载更多或分页；
- 点击行进入详情。

提交主题：`feat(web): filter and browse trace history`

F3 Phase Gate：状态映射、轮询停止、页面隐藏降频、retry/cancel、URL 筛选和浏览器测试通过。

---

### F4：MCP 状态与连通测试

#### F4.1 MCP Server 状态卡

在现有 Connection Card 中展示：

- 在线、降级、离线、配置错误；
- MCP URL 和传输协议；
- 上游 API 状态；
- 探测耗时和检查时间；
- 手动刷新。

加载失败不阻止 Key 管理；自动状态检查频率保持低频，避免管理页面制造健康探测流量。

提交主题：`feat(web): show mcp server status`

#### F4.2 连通测试步骤组件

新增可复用步骤列表：

- Connect；
- Initialize；
- Tools List；
- List Collections。

每步展示 `pending/running/success/failed/skipped`、耗时和非敏感统计。组件本身不持有 Key。

提交主题：`feat(web): render mcp connection diagnostics`

#### F4.3 创建 Key 后立即测试

扩展现有一次性 Secret Dialog：

- 保留复制 Key 和复制完整客户端配置；
- 增加“测试连接”；
- 仅点击时把当前内存 Key 发送给后端；
- 测试完成显示工具数、授权知识库数和逐步结果；
- 关闭后销毁 React state；
- 页面刷新后不可恢复 Secret，也不提供重新测试旧 Key 的入口。

禁止：URL 参数、localStorage、sessionStorage、console、analytics、错误边界上报 Key。

提交主题：`feat(web): test newly issued mcp keys`

#### F4.4 轮换 Key 后立即测试

复用 F4.3 组件，但轮换确认和结果提示独立覆盖：旧 Key 已立即失效，新 Key 必须重新交付客户端。

提交主题：`feat(web): test rotated mcp keys`

#### F4.5 MCP 故障建议展示

按后端稳定错误码映射中文建议：

- 服务不可达：检查 MCP 容器和公开 URL；
- 客户端未授权：确认使用 Client Key，不是内部共享 Key；
- 内部鉴权失败：检查 API/MCP 容器共享环境变量；
- 上游不可用：检查主 API 健康状态；
- 空授权范围：编辑该 Key 的知识库白名单；
- 超时：检查代理、网络和服务负载。

未知错误显示 Request ID，不根据英文 message 做字符串匹配。

提交主题：`feat(web): explain mcp connection failures`

#### F4.6 客户端配置模板分型

在不误导用户的前提下，为确认支持 Streamable HTTP 自定义 Header 的客户端提供模板标签；通用 JSON 保持默认。每种模板必须有 fixture 测试，禁止把内部 Key 放进模板。

若未验证某客户端格式，则只显示通用模板，不凭印象添加品牌配置。

提交主题：`feat(web): provide verified mcp client templates`

F4 Phase Gate：正确/错误/撤销/空白名单 Key 的 UI 状态、Secret 生命周期、复制、故障建议、移动端弹窗和浏览器端日志检查通过。

## 6. 推荐实现顺序

```text
F0.1 → F0.2
 ├─ F1.1 → F1.2 → F1.3 → F1.4
 ├─ F2.1 → F2.2 → F2.3
 │          F2.4 → F2.5
 │          F2.6 → F2.7 → F2.8 → F2.9 → F2.10 → F2.11
 ├─ F3.1 → F3.2 → F3.3 → F3.4 → F3.5 → F3.6
 └─ F4.1 → F4.2 → F4.3 → F4.4 → F4.5 → F4.6
```

前端实际开工依赖：

- F1 依赖后端 B1；
- F2.1–F2.3 依赖 B2.1/B2.2；
- F2.4/F2.5 依赖 B2.3/B2.4；
- F2.6–F2.11 依赖 B2.5–B2.9；
- F3 依赖 B3；
- F4 依赖 B4。

可在后端未完成时按冻结 OpenAPI 使用 mock 并行开发，但合并前必须通过真实 API 联调。

## 7. 页面级验收场景

### 文档详情

1. 打开含 100+ Chunk 的文档，只加载第一页。
2. 搜索关键词并筛选表格类型，URL 状态正确。
3. 点击结果打开抽屉，读取全文并在相邻 Chunk 间移动。
4. PDF Chunk 点击定位后，原文跳至正确页。
5. 缺定位信息时给出诚实降级说明。

### 知识库详情

1. 文件夹切换和标签组合筛选正确。
2. 长文件名、长标签、同名不同层级文件夹不破版。
3. 批量操作显示部分成功，不丢失失败项原因。
4. 刷新及浏览器回退后筛选状态一致。

### Trace

1. 运行中时间线自动更新并在终态停止轮询。
2. warning、failed、skipped、canceled 能被明确区分。
3. 失败任务可以重试并进入新 attempt。
4. 运行中任务可取消且不伪装为立即完成。

### MCP

1. 页面展示 MCP/上游分别在线或异常。
2. 新建 Key 后能完整跑通四阶段测试。
3. 错误 Key 显示客户端认证建议。
4. 内部共享 Key 不一致时提示运维检查，不向用户泄露 Key。
5. 关闭 Secret Dialog 后无法再次测试或查看完整 Key。

## 8. 每任务统一完成定义

每个任务必须：

1. 只实现一个 F 编号；
2. 有组件/工具单元测试；
3. 涉及关键用户路径时增加浏览器验收；
4. 覆盖 loading、empty、error、success；
5. 检查键盘、焦点、窄屏和 reduced-motion；
6. 不产生 console error/warning；
7. 运行 TypeScript、格式化和相关测试门禁；
8. 执行 `git diff --check`；
9. 一个清晰主题提交，不夹带后端代码和无关视觉重构。

## 9. 总体验收门禁

在 `web/` 下按项目现有脚本执行并记录：

- 类型检查；
- 单元/组件测试；
- lint/format；
- production build；
- 关键页面浏览器验收。

最终还必须确认：

- 当前知识库卡片整卡点击和强化选中/悬浮效果未回退；
- 长文档名截断未回退；
- 现有 MCP 地址、配置复制和 Key CRUD 未回退；
- 客户端 API Base URL 仍经项目既有代理/环境配置，不硬编码部署地址；
- 没有任何 Secret 进入浏览器持久化、URL 或日志。

## 10. 明确不在本计划内

- Wiki 浏览器和页面编辑器；
- 知识图谱可视化；
- Chunk 在线修改；
- 拖拽排序文件夹或文档；
- 全结果跨页选择；
- 在已有 Key 详情中重新显示 Secret；
- 浏览器直接连接内部 MCP API；
- 未经真实格式验证的第三方 MCP Client 模板。
