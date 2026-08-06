># 前端联调交接 — M3 批次 1（非 default 集合多索引 · 按集合路由）

> 交付方：B 侧（API/平台）
> 接收方：A 侧（Web 前端）
> 日期：2026-08-03
> 关联提交：`54cac7a`（M3 批次 1: EngineCache 多集合路由）
> 关联契约：[`docs/openapi/openapi.v0.2.json`](openapi/openapi.v0.2.json)（**v0.2 冻结不变，本批次无 schema 变化**）
> 前置：`handoff-2026-07-31-m2-batch3.md`

## 1. 范围与意义

**API 契约零变化**——本批次全部是后端内部修复，前端**不需要**重新 `npm run gen:types`。

修复了 v0.2 已知限制「**非 default 集合静默跑 default 索引**」（batch3 handoff §5）：现在每个集合有自己的 `HybridSearch` / `IngestionPipeline` 和独立的 Chroma 集合，查询 / 上传 / 文档列表 / 删除 / 统计都按集合路由。

| 能力 | v0.2（修复前） | M3 批次 1（修复后） |
|---|---|---|
| 查询非 default 集合 | 跑 default 索引（错的数据） | 路由到该集合自己的 dense + sparse + BM25 |
| 上传到非 default 集合 | chunk 写入 default 集合 | 写入该集合的 Chroma / BM25 / 图片 / 完整性记录 |
| 文档列表 / 详情 / 删除 / 统计 | 全集合混在一起 | 按 `(collection, source_path)` 隔离 |
| 文档详情解析 | `resolve_document` 只扫 default | 扫所有集合 |

**新增模块**：`src/application/engines.py`（EngineCache）、`src/libs/vector_store/collection_router.py`（MultiCollectionVectorStore）、`src/libs/vector_store/scoped.py`（ScopedCollectionVectorStore）。

**无新增错误码**。错误码集合同 v0.2（`COLLECTION_NOT_FOUND` / `DOCUMENT_NOT_FOUND` / `TASK_NOT_FOUND` / `QUERY_NOT_FOUND` / `INGESTION_NOT_FOUND` / `IMAGE_NOT_FOUND` / `UPSTREAM_ERROR` 等）。

## 2. 从空环境启动

与批次 2/3 完全相同（`main.py` 默认 `127.0.0.1:8766`）：

```bash
cd /home/hello/workspace/SKDY-RAG-SERVER
source .venv/bin/activate
python -m src.web_api.main
curl -sS http://127.0.0.1:8766/api/v1/system/health
```

前端照旧 `npm run dev`。**不需要重跑 `gen:types`**（契约未变）。

## 3. 可重复的测试数据（重点：非 default 集合）

```bash
python -c "from tests.smoke.gen_smoke_pdf import PAGES; import pymupdf; \
  d = pymupdf.open(); [d.new_page().insert_text((72, 72), p) for p in PAGES]; \
  d.save('/tmp/smoke.pdf')"

# 1) 建两个集合
CA=$(curl -sS -X POST http://127.0.0.1:8766/api/v1/collections -H 'Content-Type: application/json' -d '{"name":"alpha"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
CB=$(curl -sS -X POST http://127.0.0.1:8766/api/v1/collections -H 'Content-Type: application/json' -d '{"name":"beta"}'  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 2) 上传同一份 PDF 到两个集合（内容相同、分别索引）
curl -sS -X POST "http://127.0.0.1:8766/api/v1/collections/$CA/documents" -F "file=@/tmp/smoke.pdf;type=application/pdf"
curl -sS -X POST "http://127.0.0.1:8766/api/v1/collections/$CB/documents" -F "file=@/tmp/smoke.pdf;type=application/pdf"
# 轮询 task 到 succeeded（同上批次）

# 3) 两个集合各自的文档列表应当一致且独立
curl -sS "http://127.0.0.1:8766/api/v1/collections/$CA/documents" | python -m json.tool
curl -sS "http://127.0.0.1:8766/api/v1/collections/$CB/documents" | python -m json.tool

# 4) 查询 alpha → 只命中 alpha 的 chunk；删除 beta 的文档不影响 alpha
curl -sS -X POST "http://127.0.0.1:8766/api/v1/collections/$CA/queries" -H 'Content-Type: application/json' -d '{"query":"What is vector search?","top_k":5}'
```

**验证点**：非 default 集合的 query 不再返回空 citations（v0.2 时非 default 空索引 → degraded 空结果）；文档 detail / delete 走 `/api/v1/documents/{id}` 也能解析非 default 集合的文档。

## 4. 契约测试结果

执行（本批次提交之后）：

```bash
source .venv/bin/activate
python -m pytest tests/unit tests/integration -q
```

**结果：1021 passed / 18 failed**。18 项为既有基线（MCP snake_case + LLM prompt 占位，与本批次无关），与 batch3 handoff §4 一致。

**本批次新增测试**（16 个 case，全部通过）：

| 测试文件 | 条数 | 覆盖 |
|---|---:|---|
| `tests/unit/test_collection_router.py` | 4 | 真实 chromadb 下 `MultiCollectionVectorStore` 的 upsert / query / get_by_metadata / delete **按集合隔离**；`ScopedCollectionVectorStore` 转发固定 collection |
| `tests/unit/application/test_engines.py` | 5 | `EngineCache._scoped_store` 绑定集合；hybrid / pipeline 每集合缓存 |
| `tests/unit/application/test_query_service.py` | 4 | cache 后端下 `search(collection=...)` 路由；`hybrid_search` 属性拒绝 cache |
| `tests/unit/application/test_application_ingestion.py` | 5 | cache 后端下 `upload(collection=...)` worker 路由到正确 pipeline |

## 5. 设计要点（A 侧无需感知，但行为有变化）

- **查询语义**：`POST /collections/{id}/queries` 的请求/响应**完全不变**（仍同步、mode 同上）。只是后端现在按 `{id}` 解析出的集合名路由到该集合引擎。
- **`enable_rerank` 仍是 no-op**（M3 待办未动）。
- **文档 detail / delete**：`resolve_document` 现在遍历所有集合，非 default 集合的文档 id 也能解析（v0.2 只扫 default）。
- **重复文件行为**：同一内容摄取到**不同集合**各自独立记录（完整性表按 `(collection, file_hash)` 复合键）；同一集合内重复上传行为不变（同 hash 跳过 / 重传覆盖）。

## 6. 进程重启已知限制

与批次 3 完全相同，**未变化**：

| 项 | 行为 |
|---|---|
| Task 注册表 | 进程内内存 → 重启后 `task_id` 失效 → 404 `TASK_NOT_FOUND` |
| Trace 索引 | 内存 + JSONL → 重启后靠扫描恢复 |
| Upload / 文档 / 索引 / 图片 | 落盘持久化，重启不受影响 |

**新增注意**：`MultiCollectionVectorStore` 在 boot 时只 prime 启动集合（`default`），其余集合按需惰性建 Chroma 集合——首次查询/上传该集合时建，行为与自动创建一致。

## 7. 上传文件 + 本地存储清理方式

存储布局**不变**（同批次 2/3），只是非 default 集合现在也真正写进自己的索引：

```
data/
├── uploads/<collection>/...      ← 上传文件（已按集合分目录，批次 2）
├── db/bm25/<collection>.json     ← 每集合一个 BM25 索引
├── db/chroma/                    ← chromadb PersistentClient 内每集合一个 Collection
├── db/ingestion_history.db       ← v3 复合键 (collection, file_hash)
├── db/image_index.db             ← 图片行带 collection 列
├── images/<collection>/...       ← 抽取图片按集合分目录
└── traces/traces.jsonl
```

**旧数据迁移**：`ingestion_history.db` 升级为复合主键时**原地迁移**（首次连接自动执行，旧行标为 `collection='_default'`）。旧图片目录仍在 `images/_default/`，新摄取写 `images/default/`。

清理 recipe 同批次 2 §6.3 + 批次 3 §8：完全重置 `rm -rf data/`。

## 8. 前端需要的最小变更

**没有强制变更**。可选体验优化：

| 模块 | 说明 |
|---|---|
| `gen:types` | **不需要**。契约零变化 |
| 集合文档页 | 非 default 集合现在有真实的独立数据（v0.2 会混 default 内容），无需改代码即可看到正确隔离 |
| Playground | 非 default 集合查询现在有真实结果；其余不变 |

## 9. 验证清单（前端接入后跑一遍）

- [ ] 建两个集合各传一份 PDF → 两个集合文档列表各自独立
- [ ] 非 default 集合 Playground 查询 → 出 citations（不再空/错数据）
- [ ] 非 default 集合文档 detail / delete 可用
- [ ] 同内容摄取到两个集合互不影响（删除一个，另一个还在）

## 10. 反馈

- 契约冻结（v0.2）**未变**：本批次零 schema 变化，前端无需 `gen:types`。
- 已知待办（M3 剩余）：rerank 真实接入（`enable_rerank` 仍 no-op）、Query 任务化、Task/Trace 落 SQLite、`DocumentDetail.last_query_id`。
