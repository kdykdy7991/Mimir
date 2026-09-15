# 任务 08：任务治理、审计与外部同步

> 状态：待实施；前置：任务 07 Gate；后继：无

## 目标

把解析、增强、索引、维护和同步变成可恢复、可限流、可审计的持久任务，再建立安全 Connector 并交付首个
轻量增量数据源。保持 Lite 单机形态，不以 Redis 为前提。

## 原子任务

1. **08.1 状态机**：规范 parse→normalize→chunk→enrich→embed→index→finalize；记录 attempt、计数、时间、
   错误和 retry/cancel；阻止终态倒退。提交：`refactor(tasks): define durable ingestion state machine`。
2. **08.2 Worker Pool**：定义 Parse/Enrich/Index/Maintenance/Sync 队列、lease、heartbeat、并发预算；先做本地
   持久实现，重启回收过期 lease。提交：`feat(worker): add durable staged worker pools`。
3. **08.3 重试/死信/取消**：错误分类、指数退避、最大次数、dead-letter、管理员重放、revision 幂等；取消仅在
   阶段边界生效。提交：`feat(tasks): add retry dead-letter and recovery`。
4. **08.4 限流**：分别治理 DocReader、Embedding、Rerank、VLM、RPM/TPM、每 Key MCP 并发/QPS 和响应预算；
   返回 rate_limited/overloaded。提交：`feat(limits): enforce workload and mcp budgets`。
5. **08.5 审计**：追加记录 MCP 工具/collection、知识管理、Key 生命周期、版本、同步和权限拒绝；不记录完整
   query/Evidence、Secret、Header 或路径。提交：`feat(audit): record security-safe knowledge operations`。
6. **08.6 Connector/数据源**：定义 list/fetch/revision/checkpoint/deletion/test/close，统一 SourceDocument；保存
   加密凭据、collection、策略和 checkpoint。提交：`refactor(sync): define transport-neutral connector contracts`
   与 `feat(sync): persist encrypted datasource configuration`。
7. **08.7 SSRF**：强制协议、DNS/IP/CIDR、私网/metadata、逐跳重定向、下载大小/时间和日志脱敏检查。
   提交：`security(sync): enforce connector ssrf policy`。
8. **08.8 RSS/受控 URL**：连接测试、首次全量、ETag/Last-Modified、哈希、删除、重试和日志；远端更新生成
   revision，人工冲突待处理。提交：`feat(sync): add incremental rss url connector`。
9. **08.9 管理面/MCP**：管理端配置与冲突处理；MCP 只开放 list/status/failures，不开放触发或修改。
   提交：`feat(mcp): expose readonly datasource status`。
10. **08.10 演练**：新增/更新/删除、重复同步、429、超时、DNS 风险、Worker 重启、死信、人工冲突、索引失败；
    输出运行与回滚手册。提交：`test(sync): verify durable secure synchronization lifecycle`。

## 可观测性、测试与 Gate

指标覆盖队列积压/延迟、阶段/格式错误、模型 Token/限流、MCP QPS/错误/结果数、同步变更/冲突。测试覆盖
状态转换、lease 恢复、幂等、审计脱敏、凭据、IPv4/IPv6 SSRF、重定向、增量和冲突。

Gate：任务重启可恢复、死信可操作、限流稳定、审计无泄漏、同步不覆盖人工修改、连接器通过故障演练。

回滚：暂停调度和 Connector，不删除 checkpoint、版本或审计；active index 继续服务。

