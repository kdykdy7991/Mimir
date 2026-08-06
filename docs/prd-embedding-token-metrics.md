# Embedding Token 统计 PRD / 开发文档

> 日期：2026-08-06
> 目标读者：后端开发、Web 前端开发、测试人员
> 状态：待开发
> 关联接口：`GET /api/v1/metrics/overview?range=24h|7d|30d`

## 1. 背景

SKDY RAG Server 通过 MCP 提供知识库检索服务。一次查询链路只需要 Query Embedding、向量检索以及可选的 Rerank，不在服务内调用 LLM 生成答案。因此总览中的 Token 指标应统计 **Embedding 输入 Token**，而不是 LLM 的 Prompt/Completion Token。

当前使用的 `qwen3-embedding` 通过本机 OpenAI 兼容接口 `/v1/embeddings` 提供服务。实测响应已经包含：

```json
{
  "usage": {
    "prompt_tokens": 4,
    "total_tokens": 4,
    "completion_tokens": 0
  }
}
```

当前项目的 `OpenAIEmbedding.embed()` 只返回 `response.data[].embedding`，未读取和保存 `response.usage`，因此总览暂时无法显示真实用量。

## 2. 目标与非目标

### 2.1 目标

- 精确采集查询和文档索引产生的 Embedding Token。
- 支持按最近 24 小时、7 天、30 天聚合。
- 总览展示总量，并区分查询与文档索引消耗。
- 保持统计结果在服务重启后可用。
- Provider 未返回 usage 时明确标记不可用，不用 `0` 或字符数估算冒充精确数据。

### 2.2 非目标

- 不统计上游 MCP 客户端自行调用 LLM 产生的 Token。
- 不统计 Reranker Token。
- 本期不做费用换算、预算告警、按用户计费。
- 本期不补算上线前的历史 Token。

## 3. 指标定义

| 字段 | 含义 | 数据来源 |
|---|---|---|
| `embedding_token_usage` | 统计周期内 Embedding Token 总量 | 查询与文档索引之和 |
| `query_embedding_tokens` | 查询文本向量化消耗 | MCP/Web 查询链路中的 Query Embedding |
| `ingestion_embedding_tokens` | 文档 Chunk 向量化消耗 | 上传、重建索引、重新处理文档的 Chunk Embedding |

必须满足：

```text
embedding_token_usage
= query_embedding_tokens
+ ingestion_embedding_tokens
```

示例：

```json
{
  "embedding_token_usage": 128540,
  "query_embedding_tokens": 8540,
  "ingestion_embedding_tokens": 120000
}
```

### 3.1 统计口径

- 使用 Provider 返回的 `usage.total_tokens`；Embedding 场景下通常等于 `usage.prompt_tokens`。
- 一次请求包含多个输入文本时，记录该批次响应的总 Token，不按向量数量平均。
- 查询完成 Query Embedding 后，即使后续为空召回或检索失败，已经实际消耗的 Token仍需统计。
- Provider 请求失败且未返回 usage 时不计数；若 Provider 明确返回 usage，则按实际返回记录。
- 自动重试的每次成功 Provider 调用均会产生真实计算，应分别计数，避免低估资源消耗。
- 文档重新上传、重新切分、重建索引产生的新调用应重新统计。
- 系统健康检查产生的 `embed_single("ping")` 必须排除。
- 时间以 Embedding Provider 调用成功并获得 usage 的时间为准，统一保存 UTC。
- 查询和文档索引之外的新来源不得默认归类；需先扩展 operation 枚举和指标契约。

### 3.2 空值与零值

- `null`：统计能力未接入、Provider 不返回 usage，或该时间范围的数据完整性不可保证。
- `0`：统计能力正常，且该时间范围确实没有消耗。
- 上线前历史数据不补算；接口应提供数据起算时间，让前端提示统计范围。

## 4. API 契约

在 `TrafficMetrics` 中返回：

```json
{
  "traffic": {
    "request_count": 128,
    "previous_request_count": 112,
    "success_rate": 99.2,
    "average_latency_ms": 860,
    "embedding_token_usage": 128540,
    "query_embedding_tokens": 8540,
    "ingestion_embedding_tokens": 120000,
    "embedding_token_usage_since": "2026-08-06T00:00:00Z"
  }
}
```

字段约束：

| 字段 | 类型 | 约束 |
|---|---|---|
| `embedding_token_usage` | `integer \| null` | 非负，总量 |
| `query_embedding_tokens` | `integer \| null` | 非负，查询侧用量 |
| `ingestion_embedding_tokens` | `integer \| null` | 非负，入库侧用量 |
| `embedding_token_usage_since` | `datetime \| null` | 当前可用统计数据的起算时间，UTC |

三个 Token 字段应同时为数字或同时为 `null`。接口继续使用现有 `range=24h|7d|30d`，不新增请求参数。

## 5. 后端开发任务

### 5.1 扩展 Embedding 返回结构

当前 `BaseEmbedding.embed()` 只返回向量列表。需要定义统一结果结构，例如：

```python
@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    total_tokens: int | None
    model: str
    provider: str
```

- `OpenAIEmbedding.embed()` 从 `response.usage.total_tokens` 取精确值。
- 保持向量顺序与输入顺序一致。
- 其他 Provider 必须显式返回 Token 或 `None`，不能静默伪造。
- 更新所有调用方和测试替身，避免只改 OpenAI 实现导致接口不一致。

如果为控制改动风险而暂不修改公共返回类型，也可以在 Embedding 层增加 usage 回调/观测器；但采集必须发生在 Provider 响应仍可访问的位置，不能在上层按文本重新估算。

### 5.2 标注调用来源

Embedding 调用必须携带内部上下文：

```text
operation = query | ingestion | healthcheck
collection_id
query_id / trace_id（查询）
task_id / document_id（入库）
```

- Query Service 标记为 `query`。
- Dense Encoder / Batch Processor 标记为 `ingestion`。
- System Service 探活标记为 `healthcheck` 且不写业务用量。

### 5.3 持久化用量事件

建议新增 SQLite 表 `embedding_usage_events`：

```text
id                  TEXT PRIMARY KEY
occurred_at         TEXT NOT NULL       # UTC ISO-8601
operation           TEXT NOT NULL       # query | ingestion
token_count         INTEGER NOT NULL    # >= 0
provider            TEXT NOT NULL
model               TEXT NOT NULL
collection_id       TEXT NULL
trace_id            TEXT NULL
task_id             TEXT NULL
document_id         TEXT NULL
provider_request_id TEXT NULL
```

建议索引：

```text
(occurred_at)
(operation, occurred_at)
(collection_id, occurred_at)
```

写入要求：

- 每次成功取得 usage 后写一条事件。
- 写入用量失败不得导致主查询或文档处理失败，但必须记录错误日志和监控。
- 若存在应用层重试写入，使用事件 ID 或 Provider Request ID 保证同一次调用不重复入账。
- Token 数据不得仅保存在进程内存中。

### 5.4 聚合总览接口

`GET /api/v1/metrics/overview` 根据请求时间范围执行：

```sql
SELECT
  SUM(token_count) AS total,
  SUM(CASE WHEN operation = 'query' THEN token_count ELSE 0 END) AS query_total,
  SUM(CASE WHEN operation = 'ingestion' THEN token_count ELSE 0 END) AS ingestion_total
FROM embedding_usage_events
WHERE occurred_at >= :start_at AND occurred_at < :end_at;
```

- 返回值满足总量等式。
- 无事件但统计能力已启用时返回三个 `0`。
- 统计能力未启用或数据源不可用时返回三个 `null`。
- 查询不得扫描无时间索引的全表。

### 5.5 后端测试

- OpenAI 兼容响应包含 usage 时能够正确解析。
- usage 缺失时返回 `None`，主流程仍可工作。
- 单次查询正确写入 `query` 事件。
- 批量 Chunk Embedding 按每个 Provider batch 写入并正确求和。
- 健康检查不写入业务用量。
- 空召回仍统计已经消耗的 Query Embedding Token。
- 重建索引会产生新的 ingestion 用量。
- 聚合覆盖 24h、7d、30d 边界和 UTC 时间边界。
- SQLite 重启后数据仍存在。
- OpenAPI 快照测试通过。

## 6. 前端开发任务

### 6.1 总览卡片

Traffic 区域第四张卡片名称固定为 `Embedding Token`，主值展示 `embedding_token_usage`。

建议展示：

```text
Embedding Token
128.5K
查询 8.5K · 文档索引 120K
```

- 使用紧凑数字格式，如 `8.5K`、`1.2M`。
- Tooltip 或辅助说明写明：“查询文本与文档 Chunk 向量化的输入 Token”。
- 不出现 Prompt Token、Completion Token、LLM Token 等容易误导的名称。

### 6.2 状态处理

- 三个 Token 字段为数字：显示总量和两项拆分。
- 三个 Token 字段为 `null`：主值显示 `—`，说明“Embedding Token 统计未接入”。
- 值为 `0`：显示 `0`，不能转换成 `—`。
- 部分字段缺失：按契约异常处理，主值可以显示总量，拆分显示“明细不可用”，同时记录前端错误日志。
- `embedding_token_usage_since` 晚于所选周期起点时，提示“数据自 YYYY-MM-DD HH:mm 起统计”，避免把部分周期数据误认为完整周期。
- 切换 24h、7d、30d 时随总览接口重新加载，不在浏览器端自行累加。

### 6.3 前端测试

- 正常数据展示总量及查询/文档索引拆分。
- 大数格式正确。
- `null`、`0`、字段缺失分别符合设计。
- 切换统计周期后展示对应响应。
- 旧版接口响应不会导致页面白屏。
- 重新生成 OpenAPI TypeScript 类型后 typecheck、unit test、E2E、build 通过。

## 7. 前后端实施顺序

1. 后端确认本 PRD 的字段和统计口径。
2. 后端完成 usage 提取、来源标注、持久化和聚合。
3. 后端更新 `docs/openapi/openapi.v0.2.json` 与接口测试。
4. 前端运行 `npm run gen:types` 更新类型。
5. 前端接入拆分数据、空值状态和起算时间提示。
6. 双方使用真实 qwen3-embedding 完成联调。

后端未交付前，前端可继续只显示 `embedding_token_usage = null` 的未接入状态，不使用 Mock 数字冒充真实指标。

## 8. 验收标准

- 发起一次已知输入的知识库查询后，查询 Token 增量等于 Provider 响应中的 `usage.total_tokens`。
- 上传并完成一份文档索引后，文档索引 Token 按所有 Embedding batch 的 usage 正确累加。
- 总量始终等于查询用量与文档索引用量之和。
- 健康检查不会改变总览 Token。
- 空召回会增加查询 Token；Provider 调用失败且无 usage 时不会增加。
- 服务重启后历史统计仍可查询。
- 24h、7d、30d 返回各自时间窗口内的数据。
- 前端能够清晰展示总量和来源拆分，不把 `null` 显示为 `0`。
- OpenAPI、后端测试、前端类型检查、单元测试和构建全部通过。

## 9. 给开发人员的任务摘要

### 后端

从 qwen3-embedding 的 OpenAI 兼容响应中读取 `response.usage.total_tokens`，按 `query` 和 `ingestion` 分类写入 SQLite，用现有 overview 时间范围聚合并返回总量、两项拆分及统计起算时间；排除 healthcheck，补齐契约和测试。

### 前端

总览 Traffic 第四张卡片展示 Embedding Token 总量，并在卡片内以弱化文字展示“查询 / 文档索引”拆分；正确处理 `null`、`0`、旧接口和部分周期数据，重新生成 API 类型并补齐测试。
