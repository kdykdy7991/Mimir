# Task 03–08 发布就绪报告

> 日期：2026-09-18
> 自动化状态：通过
> 人工状态：等待真实环境验收

## 已自动完成

- Python 单元与契约：`1787 passed, 1 skipped`。
- Python 集成：`324 passed, 15 skipped`；跳过项为显式 live endpoint / 外部 Provider 条件。
- Python E2E：`49 passed`。
- Worker、SSRF、Connector、真实 ingestion writer、管理 API 故障组：`47 passed`。
- Web：TypeScript、ESLint、`108 tests`、Next.js 生产构建全部通过。
- 配置加载、`uv.lock`、OpenAPI（74 paths）、MCP inventory（12 tools）无漂移。
- `git diff --check` 通过。
- 自动同步调度保持关闭；管理端“立即同步”只写持久队列，显式 Worker 才消费。

## 自动回归发现并修复

1. OpenAI SDK 会继承终端中的旧式 `socks://` 代理并在构造阶段失败。LLM 与 Embedding Provider 现在使用 SDK 官方 HTTP client 且不继承环境代理。
2. 对相同内容执行 `--force` 重摄取时，已 active 的 parent version 会触发 SQLite 主键冲突。现在相同 active version 按幂等重建处理，并在索引重建期间保持原 sidecar 可读。

## 仅需人工验证

### 1. 生产密钥与备份恢复

在部署平台注入 `SKDY_DATASOURCE_MASTER_KEY`（URL-safe Base64 编码的 32 字节随机值），不要写进仓库、日志或工单正文。确认：

- 应用重启后仍能解密既有数据源凭据；
- 备份包含整个 `data/`，密钥通过独立秘密管理系统备份；
- 用错误密钥启动时读取凭据 fail-closed，且响应/日志不显示密文或明文凭据。

### 2. 真实公网 Connector 灰度

选择一个可撤销、无敏感数据的 HTTPS RSS/URL 测试源：

1. 在管理页创建数据源并执行“测试连接”。
2. 点击“立即同步”，用 `.venv/bin/python -m scripts.sync_worker --once` 消费。
3. 确认文档可检索、checkpoint 前进、凭据不回显。
4. 分别制造远端新增、更新、删除并重复同步一次，确认无重复文档。
5. 人工编辑一个已同步文档后再改远端内容，确认只产生冲突、不覆盖 active 文档。

通过标准：新增/更新/删除计数正确；失败时 checkpoint 不前进；冲突只能人工确认；查询始终读取最后成功的 active index。

### 3. 真实模型与解析服务

在配置所指向的 vLLM、Embedding 和 DocReader 服务可用时执行：

```bash
RUN_LOCAL_ENDPOINT_TESTS=1 .venv/bin/python -m pytest -q \
  tests/integration/test_local_endpoints.py
```

并用至少一份真实但非敏感的业务文档检查解析顺序、表格数字、图片/OCR 和检索相关性。机器测试负责协议与维度，内容质量仍需业务人员判断。

### 4. 部署灰度与页面验收

- 先部署单实例、单 Worker，只启用一个测试数据源；不要直接打开自动定时调度。
- 在桌面和手机尺寸人工浏览 `/collections`、`/documents`、`/data-sources`、`/mcp-keys`。
- 验证刷新、暂停/恢复、立即同步、dead-letter 重放、冲突确认和错误提示文案。
- 观察一个完整同步周期后，再决定是否扩大数据源范围。

## 发布结论

代码、离线安全边界、契约、构建及持久任务恢复已自动验收。发布阻断项仅剩上述需要真实密钥、真实网络、真实模型或人工业务判断的四项；未完成前保持外部同步自动调度关闭。
