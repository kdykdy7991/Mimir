># 前端联调交接 — M2 批次 1+2（System/Collections/Documents/Upload/Task）

> 交付方：B 侧（API/平台）
> 接收方：A 侧（Web 前端）
> 日期：2026-07-31
> 关联提交：`4531996`（批次 1: 真实端点接入） · `7738ff2`（批次 2: Upload+Task）
> 关联契约：[`docs/openapi/openapi.v0.1.json`](openapi/openapi.v0.1.json)（仍为 v0.1 命名，schema 未变）
> 关联手册：[`docs/web-api-onboarding.md`](web-api-onboarding.md)

## 1. 范围

本批次已交付的真实端点（**可直接接入**）：

| 端点 | 状态 | 提交 |
|---|---|---|
| `GET /api/v1/system/info` | 真实 | `4531996` |
| `GET /api/v1/system/health` | 真实 | `4531996` |
| `GET /api/v1/collections` | 真实 | `4531996` |
| `POST /api/v1/collections` | 真实 | `4531996` |
| `GET /api/v1/collections/{id}` | 真实 | `4531996` |
| `DELETE /api/v1/collections/{id}` | 真实 | `4531996` |
| `GET /api/v1/collections/{id}/documents` | 真实 | `4531996` |
| `POST /api/v1/collections/{id}/documents` | **真实（新）** | `7738ff2` |
| `GET /api/v1/documents/{id}` | 真实（含 `last_task_id`） | `4531996` |
| `DELETE /api/v1/documents/{id}` | 真实 | `4531996` |
| `GET /api/v1/tasks/{id}` | **真实（新）** | `7738ff2` |

**仍未交付**（前端继续 Mock）：

- `POST /api/v1/collections/{id}/queries` — 批次 3
- `GET /api/v1/queries/{id}/trace` / `GET /api/v1/ingestions/{id}/trace` — 批次 3
- `GET /api/v1/images/{image_id}` — 批次 3

> v0.2 契约冻结 = 批次 3 完成后。无需等待，已交付端点即起即可联调。

## 2. 从空环境启动

### 2.1 后端

```bash
cd /home/hello/workspace/SKDY-RAG-SERVER

# 1) 虚拟环境 + 依赖（首次）
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt && pip install -e ".[local]"

# 2) 启动 Web API（默认 127.0.0.1:8766）
python -m src.web_api.main
# 等价：uvicorn src.web_api.app:app --host 127.0.0.1 --port 8766 --reload

# 3) 健康校验
curl -sS http://127.0.0.1:8766/api/v1/system/health
# -> {"status": "ok", "dependencies": []}
```

环境变量覆盖（可选用）：

| 变量 | 默认 | 用途 |
|---|---|---|
| `WEB_API_DATA_DIR` | `./data` | 上传/索引/历史数据根目录 |
| `WEB_API_UPLOAD_MAX_BYTES` | `52428800` | 单文件上限（50 MB） |
| `WEB_API_UPLOAD_ALLOWED_MIME` | `application/pdf` | 逗号分隔 MIME 允许表 |
| `WEB_API_CORS_ALLOW_ORIGINS` | `*` | 生产建议收紧 |
| `WEB_API_REQUEST_TIMEOUT_SECONDS` | `60` | 仅文档建议，不在响应里 |

### 2.2 前端

```bash
cd /home/hello/workspace/SKDY-RAG-SERVER/web
npm install
npm run gen:types      # 从 ../../docs/openapi/openapi.v0.1.json 生成 src/types/api.ts
npm run dev            # http://localhost:3000
```

类型已生成并通过 `tsc --noEmit`（批次 2 完成后跑过一次）。

## 3. 可重复的测试数据

### 3.1 示例 PDF：3 页，~2.5 KB

```bash
# 一次性生成（依赖 pymupdf）
python -c "from tests.smoke.gen_smoke_pdf import PAGES; import pymupdf; \
  d = pymupdf.open(); [d.new_page().insert_text((72, 72), p) for p in PAGES]; \
  d.save('/tmp/smoke.pdf')"
ls -la /tmp/smoke.pdf  # 2475 bytes
```

主题：Vector Search / BM25 / Reranking / MCP / Observability。3 页文本足够触发多条 chunk。

### 3.2 最小可重复流程

```bash
# 1) 创建集合
CID=$(curl -sS -X POST http://127.0.0.1:8766/api/v1/collections \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo"}' | python -c "import sys, json; print(json.load(sys.stdin)['id'])")

# 2) 上传 PDF
RESP=$(curl -sS -X POST "http://127.0.0.1:8766/api/v1/collections/$CID/documents" \
  -F "file=@/tmp/smoke.pdf;type=application/pdf")
TID=$(echo "$RESP" | python -c "import sys, json; print(json.load(sys.stdin)['task_id'])")

# 3) 轮询任务
curl -sS "http://127.0.0.1:8766/api/v1/tasks/$TID" | python -m json.tool

# 4) 文档列表 / 详情
curl -sS "http://127.0.0.1:8766/api/v1/collections/$CID/documents" | python -m json.tool
```

### 3.3 集成测试数据

`tests/integration/test_web_api_upload.py` 使用应用层 fakes（`FakeChromaStore` / `FakeImageStorage` / `FakeIntegrity`），不依赖真实 Chroma / SQLite / 图片存储，**可离线 + 跨平台运行**。

## 4. 契约测试结果

执行命令（提交 `7738ff2` 之后）：

```bash
source .venv/bin/activate
python -m pytest tests/integration/test_web_api_endpoints.py \
                tests/integration/test_web_api_upload.py \
                tests/contract/test_openapi_snapshot.py -v
```

**结果：27 / 27 passed**（0.46s）。

| 测试组 | 条数 | 覆盖 |
|---|---:|---|
| `TestSystemInfo` / `TestSystemHealth` | 3 | `/system/info` 元数据 + 不泄露密钥；`/system/health` 200 |
| `TestListCollections` / `TestCreateCollection` / `TestGetCollection` / `TestCollectionDocuments` / `TestDeleteCollection` | 8 | 5 个集合端点（含 409 重名、404 未知） |
| `TestGetDocument` / `TestDeleteDocument` | 4 | `/documents/{id}` GET/DELETE，含 `last_task_id` 字段、自定义 404 |
| `TestUploadDocument` | 3 | 202 + pending 文档 + 稳定 document_id；状态机 pending→succeeded；落盘路径 |
| `TestGetTask` | 3 | 状态机轮询；未知 404；失败任务结构化 `TaskError` |
| `TestUploadValidation` | 2 | 415 UNSUPPORTED_MEDIA_TYPE / 404 COLLECTION_NOT_FOUND |
| `TestDocumentDetailLastTask` | 1 | 文档详情中 `last_task_id` 由 task tracker 回填 |
| `test_openapi_snapshot_matches_live_app` | 1 | `app.openapi()` 与磁盘快照逐字节一致 |
| `test_snapshot_has_expected_top_level_keys` | 1 | 端点 & schema 完整性 |

完整仓库 `python -m pytest` = **1034 passed / 18 failed**（18 项为既有不相关失败：MCP snake_case 库变更 6 项 + LLM prompt 占位符 12 项，与本批次无关）。

前端质量命令：

```bash
cd web
npm run typecheck     # tsc --noEmit —— 通过
npm run lint          # eslint —— 通过
npm test              # vitest 3 文件 10 测试 —— 通过
npm run build         # next build —— 通过
```

## 5. Task 轮询 + 进程重启的已知限制

### 5.1 推荐轮询节奏

- 后端摄取一般 1–10s（取决于页数 + 嵌入向量化）。**建议前端 1.5s 起步，指数退避到 5s 上限**。
- 状态机：`pending → running → succeeded | failed`。首次见到 `running` 后可减缓。
- 终态保留：`task_id` 在内存中保留到进程退出；不需要主动 purge。

### 5.2 进程重启 / 已知限制（v0.1 显式不做）

| 项 | 行为 | 影响 |
|---|---|---|
| Task 注册表 | **进程内内存**（`IngestionService._tracker`） | 服务重启后所有 `task_id` 失效 → `GET /tasks/{id}` 返回 404 `TASK_NOT_FOUND` |
| Upload 文件 | 落盘到 `data/uploads/<collection>/<sanitised_filename>` | 重启后文件仍在，但 task_id 失效；前端应展示「任务状态丢失」并提示重新上传 |
| Document 完整性记录 | SQLite (`data/db/ingestion_history.db`) | 持久化；重启后文档列表、详情仍可见 |
| 向量库 + BM25 + 图片 | Chroma / 文件系统 | 持久化 |
| 自动重试 | **未实现** | 任务失败需用户重新上传；不会出现 `retrying` 状态 |
| Task 取消 | **未实现** | DELETE 端点不存在 |

> v0.2 计划：Task 注册表持久化（SQLite），跨进程可查。破坏性：会引入 `task_id` 跨重启可重现，需要前后端一起升级。

### 5.3 失败响应的结构

`GET /tasks/{id}` 在 `failed` 状态返回 200 + 体内 `error`：

```json
{
  "id": "...",
  "status": "failed",
  "error": {
    "code": "UPSTREAM_ERROR",
    "message": "ingestion failed at stage embed",
    "details": {"stage": "embed", "exception_type": "PipelineStageError"}
  },
  "finished_at": "2026-07-31T09:01:23.456Z"
}
```

前端 **必须**对 `error.code` 分支（`UPSTREAM_ERROR` / `INTERNAL_ERROR`），不要 parse `message`。完整错误码速查见 [`web-api-onboarding.md` §4](web-api-onboarding.md)。

## 6. 上传文件 + 本地存储清理方式

### 6.1 落盘布局

```
data/
├── uploads/                       ← Web API 上传落地（批次 2 引入）
│   └── <collection>/              ← 按集合名分目录
│       └── <sanitised_filename>   ← 稳定路径；同名重传是覆盖
├── db/
│   ├── ingestion_history.db       ← SQLite，文件完整性 + 摄取历史
│   ├── bm25/<collection>.json     ← 每集合一个 BM25 索引
│   ├── chroma/                    ← 向量库
│   └── image_index.db             ← 图片索引
├── images/                        ← PDF 抽取图片，filename = {doc_hash}+page+seq
├── documents/                     ← CLI `scripts/ingest.py` 写入路径（Web API 不写）
└── smoke/                         ← 烟雾测试用
```

### 6.2 关键路径与文件

- `data/uploads/<collection>/<sanitised_filename>` — 上传文件，**document_id 由此路径 + 集合名 uuid5 派生**。重传同名文件会覆盖，document_id 保持稳定。
- `data/db/ingestion_history.db` — 主真源：上传完成后由 pipeline 写入；任何重启都不影响。
- `data/db/bm25/<collection>.json` — 集合标记；空集合也有，占位 `n_docs=0`。

### 6.3 清理 recipe

| 场景 | 命令 |
|---|---|
| 清理**单次上传**（test/debug） | `rm -f "data/uploads/<collection>/<sanitised_filename>"`（**不**会自动从 ingestion_history 抹去；做完全清理需重启 pipeline 或 DELETE 集合） |
| **完全重置**（database + uploads + 索引） | `rm -rf data/` 然后 `POST /api/v1/collections` 重建 + 重新上传 |
| **只清上传 + 数据库，保留索引缓存** | `rm -rf data/uploads/ data/db/ingestion_history.db data/db/bm25/*` |
| 取消未完成 task | **不支持**（v0.1 无 DELETE /tasks 端点）；只能等 worker 跑完或重启进程 |
| 释放 worker 线程 | 重启 `python -m src.web_api.main`；进程退出后所有 daemon 线程一并回收 |

### 6.4 设置覆盖

- `WEB_API_DATA_DIR=./scratch/data` 可把整个落盘搬到 `scratch/data/`，避免污染主数据集。
- 在 CI / 测试中常用 `tmp_path` fixture 隔离，参见 `tests/integration/test_web_api_upload.py::_build_services`。

## 7. 前端需要的最小变更（对应已经交付）

| 模块 | 改动 |
|---|---|
| `src/lib/api.ts`（或类似） | 把 `BASE_URL` 指向 `http://127.0.0.1:8766/api/v1`；轮询用 `setTimeout` 而非 `setInterval`（指数退避） |
| `src/features/upload/*` | 新增 `POST /collections/{id}/documents`（multipart/form-data，`file` 字段）；返回 202 + `task_id` 后立即跳到 `/tasks/{id}` 轮询 |
| `src/features/tasks/*` | 新增 `GET /tasks/{id}` 轮询；按 `status` 分支：still-running 显示进度条，succeeded 跳转文档，failed 弹 `error.code` 选择项 |
| `src/features/documents/[id]/*` | 文档详情新增 `last_task_id` 字段（提示「最近一次摄取任务」链接） |
| `src/features/collections/*` | System / Collections / Documents 列表无 schema 改动；现有 types 即可 |

批次 3 完成后（即 `Task` / `Query` / `Image` / `Trace` 真实端点）再跑一次 `npm run gen:types` 即可拿到完整类型。

## 8. 验证清单（前端接入后跑一遍）

- [ ] `npm run gen:types` 跑通，diff 仅 `task_id` 参数 `format: uuid` 增量（无破坏）
- [ ] `npm run typecheck` 通过
- [ ] `npm run lint` 通过
- [ ] `npm test` 通过
- [ ] 浏览器打开 `http://localhost:3000` + 后端起在 8766，Collections → Create → 上传 `/tmp/smoke.pdf` → 看进度 → 落到 Documents 列表
- [ ] 断网/重启后端再轮询，看到 404 `TASK_NOT_FOUND`，前端能优雅降级

## 9. 反馈

- 实时联调问题在群里 @ B 侧。
- 契约字段问题（即使只是 nullable 调整）请走 §6 流程（[onboarding](../web-api-onboarding.md) 中的接口变更规则），不要直接改 `api.ts`。
- 现阶段 v0.1 命名仍可用；v0.2 冻结后所有引用从 `v0.1` 切换到 `v0.2`（仍由 `python -m scripts.export_openapi` 一次性重命名）。
