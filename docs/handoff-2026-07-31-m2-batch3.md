># 前端联调交接 — M2 批次 3（Query / Trace / Images 真实端点 + v0.2 契约冻结）

> 交付方：B 侧（API/平台）
> 接收方：A 侧（Web 前端）
> 日期：2026-07-31
> 关联提交：`91c05de`（批次 3: Query/Trace/Images 真实端点 + v0.2 冻结）
> 关联契约：[`docs/openapi/openapi.v0.2.json`](openapi/openapi.v0.2.json)（**v0.1 → v0.2 已重命名并冻结**）
> 关联手册：[`docs/web-api-onboarding.md`](web-api-onboarding.md)（已同步 v0.2）
> 规划与决策：`docs/plan-2026-07-31-m2-batch3.md` §4.1/§4.2（探索结果 + 设计决策）

## 1. 范围

本批次把最后三组 Stub 端点接上真实应用层，**v0.2 契约冻结**：

| 端点 | 状态 | 说明 |
|---|---|---|
| `POST /api/v1/collections/{id}/queries` | **真实（新）** | 同步检索；返回 citations + diagnostics；`query_id == trace_id` |
| `GET /api/v1/queries/{id}/trace` | **真实（新）** | 查 Query trace；未知 id → 404 `QUERY_NOT_FOUND` |
| `GET /api/v1/ingestions/{id}/trace` | **真实（新）** | 查 Ingestion trace（`id == task_id`）；task 存在但 trace 缺失 → 200 空 stages |
| `GET /api/v1/images/{image_id}` | **真实（新）** | 受控图片字节 + `Cache-Control`；未知/非法 id → 404 `IMAGE_NOT_FOUND` |

至此 **15 个端点全部真实**，无 `NotReadyError`。批次 1+2 的端点未变（见 `handoff-2026-07-31-m2-batch2.md`）。

**新增错误码**：`INGESTION_NOT_FOUND`（404）。

## 2. 从空环境启动

### 2.1 后端（与批次 2 相同）

```bash
cd /home/hello/workspace/SKDY-RAG-SERVER
source .venv/bin/activate
python -m src.web_api.main          # 默认 127.0.0.1:8766
curl -sS http://127.0.0.1:8766/api/v1/system/health
# -> {"status": "ok", "dependencies": []}
```

### 2.2 前端

```bash
cd /home/hello/workspace/SKDY-RAG-SERVER/web
npm run gen:types      # 现已读 docs/openapi/openapi.v0.2.json
npm run dev            # http://localhost:3000
```

> 类型已随本批次重新生成（`api.ts`），diff 为描述/响应文档变更，**无结构性破坏**（`tsc --noEmit` 通过）。

## 3. 可重复的测试数据

### 3.1 准备数据（沿用批次 2 的 smoke PDF + 上传流程）

```bash
python -c "from tests.smoke.gen_smoke_pdf import PAGES; import pymupdf; \
  d = pymupdf.open(); [d.new_page().insert_text((72, 72), p) for p in PAGES]; \
  d.save('/tmp/smoke.pdf')"

CID=$(curl -sS -X POST http://127.0.0.1:8766/api/v1/collections \
  -H 'Content-Type: application/json' -d '{"name":"demo"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
TID=$(curl -sS -X POST "http://127.0.0.1:8766/api/v1/collections/$CID/documents" \
  -F "file=@/tmp/smoke.pdf;type=application/pdf" \
  | python -c "import sys,json;print(json.load(sys.stdin)['task_id'])")
# 轮询直到 succeeded
```

### 3.2 Query + Trace + Image 最小流程

```bash
# 1) 查询（返回 citations + diagnostics.trace_id）
Q=$(curl -sS -X POST "http://127.0.0.1:8766/api/v1/collections/$CID/queries" \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is vector search?","top_k":5,"mode":"hybrid"}')
echo "$Q" | python -m json.tool
QID=$(echo "$Q" | python -c "import sys,json;print(json.load(sys.stdin)['query_id'])")

# 2) Query trace（query_id == trace_id）
curl -sS "http://127.0.0.1:8766/api/v1/queries/$QID/trace" | python -m json.tool

# 3) Ingestion trace（ingestion id == 上传的 task_id）
curl -sS "http://127.0.0.1:8766/api/v1/ingestions/$TID/trace" | python -m json.tool

# 4) 图片：从 citations[].images[].url 拿 image_id 取字节
IMG=$(echo "$Q" | python -c "import sys,json;d=json.load(sys.stdin);print(d['citations'][0]['images'][0]['id'])" 2>/dev/null || echo "")
[ -n "$IMG" ] && curl -sS -o /tmp/img.bin "http://127.0.0.1:8766/api/v1/images/$IMG" && file /tmp/img.bin
```

### 3.3 空查询 / 越界 / 未知 id 的错误路径

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST "http://127.0.0.1:8766/api/v1/collections/$CID/queries" \
  -H 'Content-Type: application/json' -d '{"query":""}'        # 422
curl -sS -o /dev/null -w '%{http_code}\n' \
  "http://127.0.0.1:8766/api/v1/queries/00000000-0000-0000-0000-000000000000/trace"  # 404 QUERY_NOT_FOUND
curl -sS -o /dev/null -w '%{http_code}\n' \
  "http://127.0.0.1:8766/api/v1/images/..%2fetc%2fpasswd"      # 404 IMAGE_NOT_FOUND
```

## 4. 契约测试结果

执行命令（本批次提交之后）：

```bash
source .venv/bin/activate
python -m pytest tests/integration/test_web_api_queries.py \
                tests/integration/test_web_api_traces.py \
                tests/integration/test_web_api_images.py \
                tests/contract/test_openapi_snapshot.py -v
```

**结果：16 / 16 passed**（0.5s）。新增 13 个 case + 快照测试 3 个：

| 测试组 | 条数 | 覆盖 |
|---|---:|---|
| `TestQueryHappyPath` / `TestEmptyCollection` | 3 | citation 映射（document_id 稳定、scores.fusion、images 相对 URL）；`query_id == trace_id`；空结果 degraded `no chunks matched the query` |
| `TestQueryErrors` | 3 | 404 `COLLECTION_NOT_FOUND` / 422（空 query、top_k 越界、非法 mode）/ 502 `UPSTREAM_ERROR`（embedding 失败） |
| `TestQueryTrace` | 2 | 记录的 query trace 200（stages 非空）/ 404 `QUERY_NOT_FOUND` |
| `TestIngestionTrace` | 3 | 上传 task 的 trace 200（stages=load/split/embed/upsert）/ task 无 trace → 200 空 stages / 404 `INGESTION_NOT_FOUND` |
| `TestGetImage` | 3 | 字节 + `image/png` + `Cache-Control` / 404 / path-traversal 字符集防御 |
| 快照测试 | 3 | `app.openapi()` 与 v0.2 快照逐字节一致；15+ 端点；必需路径齐全 |

完整仓库 `python -m pytest` = **1049 passed / 18 failed**（18 项为既有基线：MCP snake_case 6 + LLM prompt 占位 12，与本批次无关）。

前端质量命令全绿：`npm run gen:types`、`typecheck`、`lint`、`test`(16)、`build`。

## 5. Query 语义（同步 vs 异步、mode、rerank）

| 问题 | v0.2 决策 |
|---|---|
| 同步还是异步 | **同步**。`POST /queries` 立即返回结果；top_k=10 目标 P95 < 2s。若 M3 实测超 3s，再切「Query 任务化」（复用 TaskTracker）。路由用普通 `def`，FastAPI 丢线程池，不阻塞事件循环 |
| `mode` | `hybrid`（默认）/ `dense` / `sparse` 都真实实现。hybrid 走完整 dense+sparse+RRF；dense/sparse 绕过融合直达单路 retriever。非法 mode → 422 |
| `enable_rerank` | **v0.2 为 no-op**（rerank 尚未接入引擎流水线）；字段保留，`diagnostics.reranked_count` 恒为 `null` |
| embedding 失败 | dense mode 下抛 `EmbeddingError` → 502 `UPSTREAM_ERROR`；hybrid mode 下被引擎内部降级 → 结果里 `diagnostics.degraded: true` + 原因 |
| 非 default 集合 | **已知限制**：引擎 boot 时按单集合构建，查询任何集合都跑在 default 索引上（当前存储也只有 default）。非 default 空集合 → 空 citations + degraded |

**citation 结构**：`document_id` 由 `(集合名, chunk.metadata.source_path)` uuid5 派生（与文档端点一致）；`scores` 只填当前来源分支（hybrid→fusion / dense→dense / sparse→sparse）；`images` 是相对 URL `/api/v1/images/{id}`，**永远不会泄露本地路径**。

## 6. Trace 持久化（v0.2 决策）

- **存储**：`TraceStore` = 进程内内存索引 + JSONL 追加，文件在 `{WEB_API_DATA_DIR}/traces/traces.jsonl`。查询时内存命中；重启后 `get()` 全文扫描 JSONL 兜底 → **早期 trace 重启后仍可查**（比"部分持久化"更好）。
- **id 约定**：`query_id == trace_id`；ingestion trace 的 `trace_id == task_id`。前端拿一个 id 就能取 trace。
- **stage 过滤**：wire 上只暴露**带 `elapsed_ms` 的编排级 stage**（`query_processing` / `dense_retrieval` / `sparse_retrieval` / `fusion` / `trim` 或 `load`/`split`/`embed`/`upsert`），retriever 内部的 start/finish 子事件不上网。
- **缺 trace 的 200**：ingestion 端点里 task 存在但 trace 未记录（如功能上线前的任务）→ 200 + 空 `stages`，`error` 带任务失败信息（若有）。
- **失败的 trace**：摄取失败时也会记录（trace 能看到卡在哪个 stage）；query 失败（embedding 502）不记录 trace（客户端拿不到 query_id）。

**进程重启已知限制**（沿用批次 2 + trace 侧新增）：

| 项 | 行为 |
|---|---|
| Task 注册表 | 进程内内存 → 重启后 `task_id` 失效 → `GET /tasks/{id}` 404 `TASK_NOT_FOUND` |
| Trace 索引 | 进程内内存 + JSONL → 重启后 `get()` 靠扫描 JSONL 恢复，早期 trace 仍可查 |
| Upload / 文档 / 索引 | 落盘持久化，重启不受影响 |

## 7. Image 字节服务（v0.2 决策）

- **无显式字节上限**（单图一般 < 5 MB，PDF 抽图）；统一靠反代层防 DoS。
- **MIME**：按扩展名 allow-list 映射（png / jpg / jpeg / gif / webp / bmp）；未知扩展回退 `application/octet-stream`。
- **缓存**：`Cache-Control: public, max-age=86400`（同一 image_id 内容不变）。ETag 协商推迟到 v0.3。
- **路径安全**：`image_id` 用 `[A-Za-z0-9_-]{1,128}` 字符集校验，含 `../`、`.`、`!` 等一律 404 `IMAGE_NOT_FOUND`——不会触碰存储。

## 8. 上传文件 + 本地存储清理方式

落盘布局新增一条：

```
data/
├── traces/traces.jsonl           ← 批次 3 新增：Query + Ingestion trace（JSONL）
├── uploads/<collection>/...      ← 上传文件（批次 2）
├── db/...                        ← SQLite / BM25 / Chroma（批次 1+2）
└── images/...                    ← PDF 抽取图片
```

清理 recipe 在批次 2 handoff §6.3 基础上补充：

| 场景 | 命令 |
|---|---|
| 清 trace 日志 | `rm -f data/traces/traces.jsonl`（内存索引仍在本进程生效；重启后旧 trace 消失） |
| 完全重置 | `rm -rf data/` 重建（含 traces） |

## 9. 前端需要的最小变更

| 模块 | 改动 |
|---|---|
| `gen:types` | 已切到 `openapi.v0.2.json`；重跑后 diff 仅是描述，无类型破坏 |
| Playground | `POST /collections/{id}/queries` 真实接通：`query` / `top_k`(1-50) / `mode`；渲染 `citations`（`[1]` 角标 + 文档名 + `document_id` 链接）+ `diagnostics`（duration / degraded 提示） |
| Trace 页 | `GET /queries/{id}/trace` + `GET /ingestions/{id}/trace` 真实接通；`query_id` 从 query 响应的 `diagnostics.trace_id` 拿 |
| 图片渲染 | citation `images[].url` 是相对路径，拼 `BASE_URL` 后直接 `<img>` |
| 错误分支 | 新增分支：`INGESTION_NOT_FOUND` / `QUERY_NOT_FOUND` / `IMAGE_NOT_FOUND` / `UPSTREAM_ERROR` |

## 10. 验证清单（前端接入后跑一遍）

- [ ] `npm run gen:types` 跑通，无结构性破坏
- [ ] Playground 输入问题 → 出 citations（可点击跳文档）+ diagnostics
- [ ] 从 query 响应的 `query_id` → Trace 页看到 stages 时间线
- [ ] 上传 → `task_id` → `/ingestions/{task_id}/trace` 看到摄取时间线
- [ ] 有图的 PDF 检索 → citation 里 `images[].url` 可加载
- [ ] 空集合查询 → degraded 提示"no chunks matched the query"

## 11. 反馈

- 实时联调问题在群里 @ B 侧。
- **契约已冻结（v0.2）**：不得删字段/改类型/改枚举语义；任何变更走 `web-api-onboarding.md` §6 双方确认。
- 已知待办（M3）：rerank 真实接入、Query 任务化、Task/Trace 落 SQLite、非 default 集合多索引、DocumentDetail.last_query_id。
