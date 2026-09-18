# 任务 04：统一检索与治理过滤

> 状态：已完成
> 前置：任务 03 Gate 通过  
> 后继：任务 05

## 1. 目标

把 Dense、BM25 和 Hybrid 收敛成一只可显式选择策略的 `search_chunks`，并让标签、文件夹、格式、时间等
治理信息成为真正可用的检索过滤条件。旧 `query_knowledge_hub` 保持兼容但不再拥有独立实现。

## 2. 非目标

- 不做查询改写、HyDE 或服务端多步查询；
- 不做多 collection；
- 不做父子展开；
- 不按算法注册多只 MCP 搜索工具。

## 3. 原子任务

### 04.1 应用层 `SearchRequest/SearchResult`

字段：单 query、mode、filters、top_k、rerank、threshold、include_content、response_budget。
结果使用任务 02 Evidence，包含各分支候选、最终证据和 Warning；应用层不得依赖 MCP DTO。

提交：`refactor(search): introduce unified application search contract`

### 04.2 统一三种检索模式

将 dense、sparse、hybrid 路由到同一服务。规定 score 阈值所属阶段、稳定 tie-break、去重键、融合策略和
Rerank 降级。基础设施异常不能转成空结果。

提交：`feat(search): unify dense sparse and hybrid retrieval`

### 04.3 存储层治理过滤

把 tag、folder、include descendants、file/content/source type、updated range 和 document IDs 转成受限文档集，
尽可能在存储层过滤。定义标签 AND/OR 和根目录语义。

提交：`feat(search): apply collection-scoped governance filters`

### 04.4 注册 `search_chunks`

MCP 输入直接对应 SearchRequest 的安全子集；限制 query/top-k/filter 数量；输出 Evidence、page/budget 信息和 Warning。

提交：`feat(mcp): add unified search_chunks tool`

### 04.5 旧工具委托与兼容

`query_knowledge_hub` 转为 `search_chunks(mode=hybrid)` 适配器，保留 `n_results`、`citations`、`no_rerank`
等兼容字段；删除第二套查询实现前先做等价测试。

提交：`refactor(mcp): route legacy query through unified search`

### 04.6 诊断与评测

新增管理 CLI/API 诊断视图，展示各分支 rank、融合、Rerank 前后、过滤和截断原因。运行任务 01 全基线并提交对比。

提交：`test(search): benchmark unified retrieval and filters`

## 4. 关键语义

- `mode=dense|sparse|hybrid` 是显式参数，默认 hybrid；
- 未执行的 scores 字段为 null；
- Rerank 超时可降级到 fusion 排名，但必须返回 Warning；
- filters 与 API Key scope 取交集，永远不能扩权；
- 过滤后无命中是成功空结果，检索分支失败是错误或降级；
- 同分时按 collection/document/chunk 稳定 ID 排序。

## 5. 测试与验收

- 三模式正常、空结果、阈值、去重、同分排序；
- 标签 AND/OR、目录递归、格式、时间和组合过滤；
- 无权限过滤器不能探测资源；
- Rerank 超时、Embedding/BM25 不可用语义；
- 旧工具前后等价样本；
- Golden Set 指标不低于批准阈值。

## 6. Gate 与回滚

Gate：`search_chunks` 成为唯一新检索原语；旧工具委托且兼容；治理过滤和权限测试通过；评测无未批准回归。

回滚：旧工具适配器可临时切回旧实现，但必须保留评测证据并登记原因；不得静默维持双实现。

## 7. 完成记录（2026-09-16）

- `search_chunks` 已成为统一新检索原语，三种 mode、治理过滤、阈值、去重、稳定排序与 rerank 降级均由应用层统一服务处理；
- 旧 `query_knowledge_hub` 已改为兼容适配器；双 Client、内部 HTTP API、stdio 与 Streamable HTTP 均已贯通；
- capabilities、工具 Schema 与只读契约清单已冻结为 8 工具，`governance_filters` 和 `filters.enforced_by_tools` 已启用；
- Gate：相关单元/契约测试 97 passed；双传输、capabilities 与 Golden Set 集成测试 45 passed；inventory、compileall、diff check 通过。
