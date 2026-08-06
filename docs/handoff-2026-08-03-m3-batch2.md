># 前端联调交接 — M3 批次 2（异步查询 · Task/Trace 落 SQLite · last_query_id）

> 交付方：B 侧（API/平台）
> 接收方：A 侧（Web 前端）
> 日期：2026-08-03
> 关联提交：（本批次，见 §1）
> 关联契约：[`docs/openapi/openapi.v0.2.json`](openapi/openapi.v0.2.json)（**保持 0.2.0；纯加法刷新快照**）
> 前置：`handoff-2026-08-03-m3-batch1.md`（多集合路由）

## 1. 范围与契约影响

本批次三个任务，**全部是向后兼容的加法**，v0.2 现有字段/端点/语义零删改：

| 任务 | 契约变化 |
|---|---|
| **Query 任务化（异步查询）** | 新增 `POST /collections/{id}/queries/async` + `GET /queries/{query_id}/result`；**现有同步 `POST /queries` 完全不变** |
| **Task / Trace 落 SQLite** | 无（纯内部；顺带解除「重启后 task_id 404」的 v0.2 已知限制） |
| **`DocumentDetail.last_query_id`** | `DocumentDetail` 加可选字段 `last_query_id`（默认 `null`） |

**快照已重新导出**：`docs/openapi/openapi.v0.2.json` 现含 2 个新端点 + 2 个新 schema（`AsyncQueryAccepted` / `AsyncQueryResult`）+ `DocumentDetail.last_query_id` 字段。**A 侧需要重跑 `npm run gen:types`**（diff 全是加法，无类型破坏）。

无新增错误码。错误码集合同 v0.2。

## 2. 三个任务的动机（给 A 侧参考）

- **异步查询**：检索慢时（embedding 抖动 / 大语料）同步请求长时间占连接、前端只能转圈。异步后与上传任务统一交互：提交 → 202 → 轮询结果。
- **Task/Trace 落 SQLite**：v0.2 最大的已知限制——Task 是进程内内存，**服务器一重启 `GET /tasks/{id}` 就 404**。现在任务状态、trace、异步查询结果都落在 `data/db/web_api.db`，重启后照常可查。
- **last_query_id**：文档详情能反跳「上次引用我的查询」→ 该查询的 trace，形成文档 ↔ 查询的双向追溯（与已有 `last_task_id` 对称）。

## 3. 从空环境启动

与批次 1/2/3 相同：

```bash
cd /home/hello/workspace/SKDY-RAG-SERVER
source .venv/bin/activate
python -m src.web_api.main          # 127.0.0.1:8766
```

前端照旧 `npm run dev` + **重跑 `npm run gen:types`**。

## 4. 可重复的测试数据（异步查询 + 重启）

```bash
CID=$(curl -sS http://127.0.0.1:8766/api/v1/collections | python -c \
  "import sys,json;print([c['id'] for c in json.load(sys.stdin)['items'] if c['name']=='default'][0])")

# 1) 异步查询提交 → 202 + query_id (== task_id)
Q=$(curl -sS -X POST "http://127.0.0.1:8766/api/v1/collections/$CID/queries/async" \
  -H 'Content-Type: application/json' -d '{"query":"What is vector search?","top_k":5}')
QID=$(echo "$Q" | python -c "import sys,json;print(json.load(sys.stdin)['query_id'])")
echo "$Q" | python -m json.tool

# 2) 轮询结果（pending/running → succeeded + citations）
curl -sS "http://127.0.0.1:8766/api/v1/queries/$QID/result" | python -m json.tool

# 3) 同一 id 取 trace（query_id == trace_id）
curl -sS "http://127.0.0.1:8766/api/v1/queries/$QID/trace" | python -m json.tool

# 4) 重启后端进程，再查同一 result / trace —— 仍然可读（SQLite 持久化）
#    （重启后 task/result 不再 404；v0.2 时 task 会 404）

# 5) last_query_id：同步查询引用过某文档后，文档详情带上最近查询
curl -sS -X POST "http://127.0.0.1:8766/api/v1/collections/$CID/queries" \
  -H 'Content-Type: application/json' -d '{"query":"What is vector search?"}' >/dev/null
DID=$(curl -sS "http://127.0.0.1:8766/api/v1/collections/$CID/documents" | python -c \
  "import sys,json;print(json.load(sys.stdin)['items'][0]['id'])")
curl -sS "http://127.0.0.1:8766/api/v1/documents/$DID" | python -c \
  "import sys,json;print('last_query_id =', json.load(sys.stdin)['last_query_id'])"
```

## 5. 契约测试结果

```bash
source .venv/bin/activate
python -m pytest tests/unit tests/integration tests/contract -q
```

**结果：1038 passed / 18 failed**（18 项为既有基线：MCP snake_case + LLM prompt 占位，与本批次无关）。

**本批次新增测试**（15 个，全部通过）：

| 测试文件 | 条数 | 覆盖 |
|---|---:|---|
| `tests/integration/test_web_api_m3_batch2.py` | 9 | async 提交/轮询/结果/trace；未知 query 404；失败 → `UPSTREAM_ERROR`；query 任务不被 `/tasks` 服务；`last_query_id`（命中 + 未命中）；**任务与异步结果跨「重启」可查**（同一 SQLite 重建 services） |
| `tests/unit/application/test_web_store.py` | 6 | WebApiDB 表 CRUD + 跨实例恢复；TaskTracker 写穿 + 重启恢复；`task_type` 默认值；error/progress 往返 |

快照测试 `tests/contract/test_openapi_snapshot.py` 通过（与重导出文件逐字节一致）。

## 6. 异步查询语义（新增端点）

| 项 | 行为 |
|---|---|
| `POST /collections/{id}/queries/async` | 202 `AsyncQueryAccepted{query_id, task_id, status:"accepted"}`。**`query_id == task_id == trace_id`**，一个 id 驱动 result + trace |
| `GET /queries/{query_id}/result` | 轮询：`pending`/`running` → 继续轮询；`succeeded` → 带 `result`（完整 `QueryResponse`）；`failed` → 带 `error`（v0.1 错误形状）。未知 id → 404 `QUERY_NOT_FOUND` |
| `GET /queries/{query_id}/trace` | 对异步查询同样可用（同一 id） |
| `mode` | `hybrid` / `dense` / `sparse` 语义与同步一致；`dense` 下 embedding 失败 → 任务 `failed` + `UPSTREAM_ERROR` |
| 同步 `POST /queries` | **完全不变**，仍即时返回 |

## 7. 持久化（SQLite）与已知限制更新

`data/db/web_api.db` 四张表：`tasks`（含 `task_type`）、`traces`、`query_results`、`query_citations`。写入是尽力而为的写穿（`TaskTracker.update` 每次变更落库）。

**v0.2 已知限制 → 本批次状态**：

| 项 | v0.2 | M3 批次 2 |
|---|---|---|
| Task 注册表 | 进程内内存，重启后 task_id 404 | **SQLite 持久化，重启后仍可查** ✅ |
| Trace 查找 | 内存 + JSONL 全文扫描 | 内存 + SQLite 索引（JSONL 保留兼容） ✅ |
| 异步查询结果 | 不存在 | SQLite 持久化，重启后仍可读 ✅ |

**仍保留的限制**：自动重试未实现；任务取消未实现（无 `DELETE /tasks`）；`/tasks/{id}` 仍只服务摄取任务（query 任务走 `/queries/{id}/result`）。

## 8. 上传文件 + 本地存储清理方式

新增一条存储：

```
data/
└── db/web_api.db        ← 本批次新增：tasks / traces / query_results / query_citations
```

清理 recipe：`rm -f data/db/web_api.db`（任务/trace/异步结果清空）；完全重置 `rm -rf data/`。

## 9. 前端需要的最小变更

| 模块 | 改动 |
|---|---|
| `gen:types` | **必须重跑**。新增 2 端点 + 2 schema + `DocumentDetail.last_query_id`，全部加法 |
| Playground（可选） | 慢查询可切到 `POST /queries/async` + 轮询 `/queries/{id}/result`；默认仍走同步 |
| 文档详情（可选） | `last_query_id` 非空时，可加「最近引用该文档的查询」链接 → 该查询 Trace 页 |
| 上传轮询 | 行为不变（体验更稳：重启不再丢任务） |

## 10. 验证清单（前端接入后跑一遍）

- [ ] `npm run gen:types` 跑通，无类型破坏
- [ ] 异步查询：提交 → 轮询 → succeeded 拿 citations；同一 id 取 trace
- [ ] **重启后端后**，旧 task / 异步结果仍可查
- [ ] 上传后轮询任务在重启后不丢
- [ ] 文档详情 `last_query_id`：查询命中后非空，可跳对应 trace

## 11. 反馈

- 契约 v0.2 **保持 0.2.0**：本批次全部是加法，未删字段/改类型/改枚举语义。
- 已知待办（M3 剩余）：**rerank 真实接入**（`enable_rerank` 仍 no-op）。
