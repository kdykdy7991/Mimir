# Task 08 运行与回滚手册

## 1. 默认运行姿态

- 外部同步调度默认关闭；完成生产灰度前不要用定时器自动触发 Sync 队列。
- Lite 部署只依赖 SQLite 与本地文件。队列、数据源、checkpoint、冲突和审计文件必须纳入同一备份策略。
- 创建数据源前必须配置 `SKDY_DATASOURCE_MASTER_KEY`，值为 URL-safe Base64 编码的 32 字节密钥。缺失或错误时写操作 fail-closed。
- MCP 只开放数据源列表、状态和失败查询，不开放配置、同步触发或冲突写操作。
- 管理页“立即同步”只向 SQLite Sync 队列入队；必须显式启动 Worker 才会抓取远端内容。自动定时调度仍默认关闭。

显式处理一个已入队任务：

```bash
.venv/bin/python -m scripts.sync_worker --once
```

灰度期间以单 Worker 持续消费（Ctrl-C 安全停止，lease 到期后可恢复）：

```bash
.venv/bin/python -m scripts.sync_worker --poll-seconds 1
```

## 2. 上线前检查

1. 运行 Task 08 定点 Gate 与 OpenAPI 漂移检查。
2. 确认 `/api/v1/data-sources` 响应不含 `credentials`、token、password 或主密钥。
3. 用公网测试域验证 RSS/URL；不得使用 loopback、私网、link-local、metadata IP、非 HTTPS 或未允许端口。
4. 创建测试数据源后执行暂停/恢复，确认 checkpoint revision 不变。
5. 检查 MCP Key 只能看到其 collection 白名单内的数据源。
6. 在启用任何调度前备份 `data/`，并记录备份时间与应用版本。

## 3. 故障处置

### Worker 重启与 lease

停止 Worker 后保留数据库。重启时只回收 lease 已过期的任务；未过期 lease 不应被第二个 owner 抢占。确认事件历史包含领取、心跳、恢复或完成，且 attempt 没有被覆盖。

### 重试与 dead-letter

瞬时错误进入有上限的指数退避；永久错误或耗尽 attempts 进入 `dead_letter`。重放只允许从 dead-letter 发起，并保留既有 attempt/事件记录。原始异常正文不得写入队列或审计，只保存稳定错误分类。

### 429、过载与上游超时

- MCP RPM 超限返回 `rate_limited` 和可用的 Retry-After。
- 并发预算耗尽返回 `overloaded`；客户端按 Retry-After 退避，不立即并发重试。
- Connector 连接、读取或总超时按瞬时错误处理；达到最大 attempts 后进入 dead-letter。
- 429/超时期间不得推进 checkpoint。

### DNS、重定向与下载风险

每次请求和每一跳重定向都重新解析全部 A/AAAA 结果。任一结果不属于 global 地址即拒绝；禁止依赖环境代理。响应超过声明或流式字节上限时立即停止，且不得记录 URL userinfo、认证 Header 或响应正文。

### 人工修订冲突

远端更新不得覆盖人工 active revision。管理页展示当前与远端 revision；操作员在文档版本界面完成取舍后，才可点击“标记已人工处理”。该按钮仅确认处置，不创建、不激活远端 revision。

### 索引失败

保持上一 active revision 与索引继续服务。失败 revision 保持 `index_failed`，不得推进同步绑定或把新 checkpoint 宣告成功。修复索引依赖后通过 revision 重建流程重试。

## 4. 回滚

1. 首先暂停全部数据源并停止 Sync Worker/调度器。
2. 不删除或清空 queue、dead-letter、checkpoint、revision、冲突或审计数据库。
3. 保持当前 active index 在线，确认查询与 MCP 旧工具仍可读取。
4. 如需回退应用版本，先备份整个 `data/`；旧版本不得写入包含新迁移列的数据库，除非已验证向后兼容。
5. Web 管理页可从导航撤下，但 MCP 三个只读工具若已被客户端采用，应通过版本化发布处理，不直接破坏 Schema。
6. 恢复时先启用单个测试数据源，验证 checkpoint、重复同步幂等、冲突与审计，再逐步恢复其余数据源。

## 5. 数据保全与密钥

- 数据源主密钥不进入数据库、日志、审计或前端响应。
- 丢失主密钥后现有凭据不可恢复；不要通过删除数据库“修复”。从安全备份恢复密钥，或重新创建数据源。
- 当前版本没有在线主密钥轮换流程。轮换前保持调度关闭，并使用后续专用迁移工具完成逐条解密/重加密；不得直接替换环境变量。
- 审计 hash-chain 校验失败视为安全事件，保留原文件并停止安全敏感管理写操作。

## 6. Gate 命令

```bash
.venv/bin/python -m pytest -q \
  tests/unit/application/test_task_state_machine.py \
  tests/unit/application/test_worker_pool.py \
  tests/unit/application/test_audit_store.py \
  tests/unit/test_connector_store.py \
  tests/unit/test_connector_http_security.py \
  tests/unit/test_rss_url_connectors.py \
  tests/unit/test_data_sources_api.py \
  tests/unit/test_mcp_contract_snapshot.py \
  tests/unit/test_mcp_contract_v1_snapshot.py \
  tests/integration/test_mcp_capabilities_transports.py
.venv/bin/python -m scripts.export_openapi --check
git diff --check
cd web
npm run typecheck
npm run lint
npm test
npm run build
```

验收证据必须记录命令、通过数、已知 warning 和未执行项。任何依赖真实公网、模型或生产凭据的演练必须在隔离环境单独记录，不用单元测试结果替代。
