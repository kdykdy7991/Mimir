# Task 08 开发契约：任务治理、审计与外部同步

> 状态：已完成
> 日期：2026-09-17
> 前置：Task 07 Gate 已通过

## 1. 交付目标与边界

把解析、增强、索引、维护和外部同步统一为可恢复、可限流、可审计的本地持久任务，并交付首个安全的 RSS/受控 URL 增量数据源。Lite 部署继续只依赖 SQLite 和本地文件，不强制 Redis、Celery 或外部消息队列；MCP 仅增加数据源只读状态能力，不开放同步触发、配置或冲突处理写操作。

## 2. 现状审计

- `TaskTracker` 已向 SQLite 写穿，上传任务具有 pending/running/terminal、人工 retry 与协作取消，但执行仍由临时线程承担，重启后不会重新领取未完成任务；
- ingestion 公开进度仍是 load/split/transform/embed/upsert，尚无 parse→normalize→chunk→enrich→embed→index→finalize 的内部治理阶段、lease、heartbeat、自动退避或 dead-letter；
- 摄取并发已有进程内 `BoundedSemaphore(4)`，MCP 已有响应大小/条数预算和 rate_limited/overloaded 错误契约，但没有按工作负载、Key、RPM/TPM 执行的真实预算；
- trace 与 usage 指标可辅助诊断，但不是安全审计账本；当前没有统一审计脱敏策略、Connector 契约、加密数据源凭据、checkpoint、SSRF 策略或同步冲突模型；
- Task 07 的不可变 revision 和补偿式索引激活可作为远端更新、人工冲突和同步失败不覆盖旧 active 的一致性基础。

## 3. 持久任务与 Worker 契约

- 内部阶段固定为 parse、normalize、chunk、enrich、embed、index、finalize；公开旧进度字段保持兼容，由映射层转换；
- 任务记录 queue、stage、attempt/max_attempts、计数与时间、错误分类、next_retry_at、cancel_requested、lease owner/expiry 和 heartbeat；终态不可回退；
- Parse、Enrich、Index、Maintenance、Sync 为逻辑队列，各自有并发预算；SQLite 原子 claim，过期 lease 在重启后回收，同一 revision/同步变更必须幂等；
- 取消只在阶段边界生效。瞬时故障指数退避并带上限，永久故障直接 dead-letter；管理员重放创建可审计的新 attempt，不改写历史结果。

## 4. 限流与审计契约

- DocReader、Embedding、Rerank、VLM 分别限制并发，并为具备 usage 的模型执行 RPM/TPM；MCP 按 Key 限制并发/QPS和响应预算；拒绝必须稳定映射为 `rate_limited` 或 `overloaded` 并携带可用的 Retry-After；
- 审计为追加写，覆盖 MCP 工具与 collection、知识管理写操作、Key 生命周期、revision、同步和权限拒绝；记录 actor/action/resource/result/request-id/时间及安全元数据；
- 永不记录完整 query、Evidence 正文、Secret、Authorization/Cookie/Header、源文件绝对路径或 Connector 凭据。审计写失败不得静默伪装成功，对安全敏感管理操作采用 fail-closed。

## 5. Connector、同步与 SSRF 契约

- Connector 统一提供 list/fetch/revision/checkpoint/deletion/test/close，输出 transport-neutral `SourceDocument`；数据源绑定 collection、策略、加密凭据和 checkpoint；
- SSRF 校验覆盖 scheme、hostname、DNS 全部解析结果、IPv4/IPv6、私网/loopback/link-local/metadata/CIDR，且每次重定向逐跳复验；限制响应大小、连接/读取/总超时并脱敏日志；
- RSS/受控 URL 支持连接测试、首次全量、ETag/Last-Modified、内容 hash、删除、重试与 checkpoint。远端变化生成 revision；遇到人工修改不覆盖 active，进入人工冲突；
- 暂停 Connector 或调度不得删除 checkpoint、revision、dead-letter 或审计，现有 active index 继续提供读取服务。

## 6. 管理面与 MCP 边界

- 管理 REST/Web 提供数据源配置、连接测试、暂停/恢复、状态、失败、dead-letter 重放和冲突处理；凭据只写不回显；
- MCP 仅提供 `list_data_sources`、`get_sync_status`、`list_sync_failures` 三个只读工具，并继承 Key 的 collection 白名单；无权与不存在保持同形；
- 新能力进入 capabilities、OpenAPI、TypeScript 类型和冻结契约快照，旧九个只读工具行为保持兼容。

## 7. 实施顺序与 Gate

1. 持久状态机、迁移和终态保护；
2. SQLite Worker Pool、claim/lease/heartbeat 与重启恢复；
3. 错误分类、退避、dead-letter、取消和重放；
4. 工作负载/MCP 预算与稳定错误映射；
5. 追加式安全审计与管理查询；
6. Connector/数据源模型、凭据加密和 checkpoint；
7. SSRF 策略及受控 HTTP transport；
8. RSS/URL 增量同步、revision/冲突接线；
9. 管理面与 MCP 三个只读工具；
10. 故障演练、运行/回滚手册、契约/性能/安全 Gate。

Gate 覆盖状态转换、原子 claim、lease 恢复、幂等、退避/死信、审计脱敏、凭据不回显、IPv4/IPv6 与重定向 SSRF、增量新增/更新/删除、重复同步、人工冲突和索引失败。Task 08 完成前默认不开启外部同步调度。

## 8. 实施记录

- 2026-09-17：完成现状审计并冻结开发契约，Task 08 开始实施。
- 2026-09-17：完成 08.1 第一批。新增统一内部阶段映射、显式生命周期转移校验和终态保护；SQLite 任务表开始持久化 execution stage、阶段起始时间与取消请求，旧库启动时增量迁移且公开 v0.2 DTO 保持兼容。
- 2026-09-17：完成 08.2 存储与调度内核。新增 Parse/Enrich/Index/Maintenance/Sync 五类 SQLite 持久队列、幂等 enqueue、`BEGIN IMMEDIATE` 原子 claim、owner-bound heartbeat/completion、过期 lease 自动回收及进程重启恢复；本地 `DurableWorkerPool` 以注册 handler 单次调度，后续阶段接入退避/死信与生产任务提交。
- 2026-09-17：完成 08.3 队列失败治理。瞬时失败按 attempt 指数退避并在最大次数后进入 dead-letter，永久失败直接进入 dead-letter；管理员重放保留同一任务的完整事件/attempt 历史并扩展尝试预算。queue + idempotency key 唯一约束阻止同一 revision 重复执行，worker 边界统一完成错误分类且不持久化原始异常文本。
- 2026-09-17：完成 08.4 第一批预算内核与 MCP 接线。新增 DocReader/Embedding/Rerank/VLM/MCP 独立并发预算及精确滚动 RPM/TPM 计数；MCP 按 key_id 隔离并发和 RPM，超额稳定返回 `rate_limited`（含 Retry-After）或 `overloaded`，既有响应条数/字符预算继续生效。模型预算以内核 API 提供，具体 provider 接线随对应生产 worker 注册完成。
- 2026-09-17：完成 08.4 Provider 接线。Web API composition 对共享 Embedding/LLM 实例安装透明预算 adapter，文本与含图消息分别进入 Rerank/VLM 预算；EngineCache 将同一 limiter 传至真实 DocReader client。只有调用方或 Provider 能给出精确 token cost 时才扣 TPM，未知 usage 不估算为 0；CLI 独立进程仍沿用自身现有 parser 并发配置。
- 2026-09-17：完成 08.5 第一批安全审计。新增 SQLite 追加式 hash-chain 审计账本和集中递归脱敏，禁止 query/Evidence/content、Secret、Header、Token 与路径进入 metadata；MCP 调用只记录 key/tool/outcome，不记录参数，并覆盖限流、过载、拒绝和异常；管理 API Key 的创建、撤销、删除、重命名、scope 更新和轮换均记录 secret-free 生命周期事件。
- 2026-09-17：完成 08.5 Web 管理边界。审计账本进入 ApplicationServices；统一 middleware 覆盖所有 `/api/v1` 写操作和 401/403，包括知识库、文档、标签、文件夹、revision、增强、任务与后续同步路由。事件仅含路由模板、受控资源 ID、collection、actor、request-id、状态码和 outcome，不读取请求体、查询串或敏感 Header。
- 2026-09-17：完成 08.6 契约与持久层。冻结 Connector 的 test/list/fetch/checkpoint/close、增量 item/deletion 与统一 SourceDocument；数据源保存 connector type、collection、策略和 CAS checkpoint。凭据使用外部主密钥驱动的 AES-256-GCM（source ID 为 AAD）加密，数据库永不保存明文，管理读取模型不含凭据；缺少主密钥时 fail-closed。
- 2026-09-17：完成 08.7 SSRF 边界。受控 HTTP 默认仅 HTTPS/443，拒绝 userinfo、异常 authority 和非允许端口；DNS 全部 A/AAAA 结果必须为 global，显式覆盖 IPv4/IPv6 loopback/private/link-local、metadata 与 IPv4-mapped IPv6。客户端禁用环境代理和自动重定向，每一跳重新解析验证，并限制跳数、连接/读取/总超时、声明及流式实际下载字节。
- 2026-09-17：完成 08.8 Connector 内核。受控 URL 支持 ETag/Last-Modified 条件请求与内容 hash 去重；RSS/Atom 使用 hardened XML parser，按 guid/id/link 稳定标识，输出新增/更新和远端删除 tombstone，并保存 seen/revision checkpoint。同步绑定记录 last synced revision；若 active 已被人工修改，远端更新只写 pending conflict 且不调用 revision writer，否则通过注入的 writer 生成新 revision 并推进绑定。
- 2026-09-17：完成 08.9 第一批管理 REST。新增数据源创建、列表、暂停/恢复、状态与失败查询；凭据只在创建请求进入加密层，所有响应永久排除凭据。同步 run 只保存稳定 error code 和新增/更新/删除/冲突计数，为管理 UI 与后续 MCP 三项只读工具提供同一数据源。
- 2026-09-18：完成 08.9 只读 MCP 链路。`list_data_sources`、`get_sync_status`、`list_sync_failures` 已贯通 InProcess Client、认证内部 HTTP、HTTP Client 与 MCP registry；三层均按当前 Key collection 白名单过滤，无权与不存在同形。只读 Client/工具从 9 扩至 12，内部路径从 12 扩至 15，Phase 0、v1 inventory 与 capabilities 冻结快照已审查更新；MCP 仍无同步触发、配置或冲突写工具。
- 2026-09-18：完成 08.9 管理 Web 与冲突确认闭环。新增 `/data-sources` 页面、导航、创建（凭据只写）、暂停/恢复、最近运行、失败与待处理冲突视图；新增冲突列表及幂等 `acknowledge` 管理 REST。确认动作只记录“已人工处理”，不会隐式创建或激活远端 revision，避免 UI 操作绕过 Task 07 的 revision/索引一致性边界。OpenAPI 已刷新至 70 paths，前端生成类型、typecheck、lint、Vitest 与 Next.js 生产构建通过。
- 2026-09-18：开始 08.10 演练与收口。新增 Worker/数据源运行和回滚手册，冻结默认关闭同步调度、lease 恢复、dead-letter、429/超时、DNS/重定向、人工冲突、索引失败、数据保全及密钥处置步骤；真实公网与生产凭据演练仍须在隔离环境留证，不能用单元测试代替。
- 2026-09-18：完成 08.10 第一轮全量回归。Python unit+contract 为 1768 passed / 1 skipped，integration 为 324 passed / 15 skipped；显式 live-endpoint Gate 现在仅在 `RUN_LOCAL_ENDPOINT_TESTS=1` 时运行，避免端口上无关服务造成伪失败。同步修正统一 LLM messages、MCP SDK `mime_type`、无权/不存在同形和 100 文件/扩展白名单的旧测试断言。故障回归发现任务终态与 stale worker 竞态，以及 `succeeded` 先于 BM25/cache 可见性的窗口；worker 现会在入口和进度边界停止已结算任务，并在发布成功终态前完成 reader/cache 失效。
- 2026-09-18：补齐连接测试、同步编排和 dead-letter 管理骨架。`DataSourceSyncService` 从加密存储读取配置，构造 RSS/URL Connector，逐项 heartbeat/apply，整批成功后才以 CAS 推进 checkpoint，并写入 running→succeeded/conflict/failed run；暂停源不会创建运行记录。Bearer Token 仅发往初始同源地址，跨 origin 重定向自动剥离。新增连接测试、按数据源过滤的 Sync dead-letter 查询/幂等重放 REST 及 Web 操作，响应不暴露凭据或原始 worker payload；OpenAPI 更新至 73 paths。实际 revision/document applier 仍需接入现有文档写入和 Task 07 索引激活链路后才可启用调度。
- 2026-09-18：新增整文级 `RevisionAwareDocumentApplier` 边界，显式解决 Connector 整文与 Task 07 Chunk revision 粒度不同的问题。applier 以稳定 source/external ID 绑定文档，只有注入的整文 writer 完成解析、分块、多索引激活并返回 committed revision 后才推进 binding；active 已偏离 last-synced 时只创建冲突，writer 失败不留 binding。连接测试现会真实执行受限下载和格式解析但不应用内容、不推进 checkpoint，并将 SSRF/配置、超时和上游错误映射为不泄漏 URL/IP 的稳定管理错误。
- 2026-09-18：完成真实同步写入与显式 Worker 接线。`IngestionDocumentWriter` 将 source/external ID 映射为稳定 canonical 文档身份，调用既有上传流水线并等待任务成功及 parent sidecar active version 后才确认 committed revision；失败、超时和索引未激活均不推进 binding/checkpoint，删除走现有跨存储协调删除，远端删除后重建不误判为人工冲突。生产 composition 注册 Sync handler 到持久 Worker Pool，但不自动启动调度。管理 REST/Web 新增幂等“立即同步”，只 enqueue 当前 checkpoint 的任务并返回 202；运维通过 `scripts.sync_worker` 显式单次或持续消费。OpenAPI 更新至 74 paths，自动调度仍默认关闭。
- 2026-09-18：Task 08 离线 Gate 完成并关闭。定点单元/契约 69 passed，MCP stdio/HTTP 双传输 2 passed，新增同步链路聚焦 23 passed；OpenAPI 74 paths 无漂移，前端 typecheck、lint、108 tests 与 Next.js 生产构建通过，`git diff --check` 通过。此前全量 Gate 证据为 unit+contract 1768 passed / 1 skipped、integration 324 passed / 15 skipped。真实公网、生产凭据和生产网络策略演练仍按运行手册在隔离上线环境留证，不在开发测试环境伪造。
- 2026-09-18：发布前最终自动回归为 unit+contract 1787 passed / 1 skipped、integration 324 passed / 15 skipped、E2E 49 passed、同步故障组 47 passed、Web 108 passed。回归修复两项发布缺陷：OpenAI SDK 不再因继承旧式 ambient SOCKS 代理而构造失败；相同内容 `--force` 重摄取的 active parent version 改为幂等，不再撞主键且重建期间保持可读。人工项目收敛为生产密钥/恢复、真实公网 Connector、真实模型/业务文档质量和部署页面灰度四项，详见 `docs/release-readiness-task-03-08.md`。
