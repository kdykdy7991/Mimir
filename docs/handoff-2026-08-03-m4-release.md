># 发布交接 — M4 收口（真实健康检查 · 文档同步 · 发布验收）

> 交付方：B 侧（API/平台）
> 接收方：A 侧（Web 前端）
> 日期：2026-08-03
> 关联提交：（本批次，见 §1）
> 前置：`handoff-2026-08-03-m3-batch2.md`

## 1. 范围与意义

本批次完成 M4 发布收口的三个收尾项，**契约零变化**（`/system/health`、`/system/info` 的 schema 不变，内容变为真实探测）：

1. **真实 `/system/health` + Provider `ready`**（替代 M1 以来的假健康 stub）
2. **文档同步**（README / onboarding / 职责进度 / PRODUCTION spec，消除过期描述）
3. **M4 发布验收**（统一启动脚本 + 可选容器 + 性能基线 + 全量回归）

同时修复了验收中暴露的**两个真实迁移 bug**（见 §5）。

## 2. 真实健康检查语义（契约不变，内容变真实）

`GET /system/health` 现在做**真实依赖探测**（带 5s TTL 缓存，避免每次打 embedding API）：

| 依赖 | 探测方式 |
|---|---|
| `embedding` | 实际调用一次 `embed_single("ping")`；抛 `EmbeddingError` → `down` |
| `chroma` | 对 `MultiCollectionVectorStore` 底层 PersistentClient 执行 `heartbeat()` |
| `sqlite` | 对 `data/db/web_api.db` 执行 `SELECT 1` |
| `bm25` | 校验 `<data_dir>/db` 可写（稀疏索引为本地落盘） |

`GET /system/info` 的 Provider `ready` 语义：

- `embedding.ready` ← **真实探测结果**（不再是恒 `True`）
- `llm` / `vision` → `ready = configured`（不在检索热路径；**修复了「未配置但 ready=True」的 bug**）

本机验收结果（embedding vLLM 在跑）：

```json
{"status": "ok", "dependencies": [
  {"name": "embedding", "status": "ok"}, {"name": "chroma", "status": "ok"},
  {"name": "sqlite", "status": "ok"},   {"name": "bm25", "status": "ok"}]}
```

## 3. 统一启动与部署（M4 新增资产）

| 资产 | 作用 |
|---|---|
| [`Makefile`](../Makefile) | `make dev`（后端+前端一起）/ `make api` / `make web` / `make test` / `make benchmark` / `make docker-up` |
| [`Dockerfile`](../Dockerfile) + [`docker-compose.yml`](../docker-compose.yml) + [`.dockerignore`](../.dockerignore) | 可选容器部署（后端；`network_mode: host` 直达本机 embedding） |
| [`scripts/benchmark.py`](../scripts/benchmark.py) | 查询延迟性能基线 |

## 4. 发布验收结果

| 验收项 | 结果 |
|---|---|
| **全量回归** | `tests/unit + integration + contract` = **1046 passed / 18 基线失败**；`tests/e2e` = **42 passed** |
| **启动流程** | `python -m src.web_api.main` → `/system/health` 4 依赖全 ok |
| **示例数据** | 上传 smoke PDF → 摄取成功 → 集合文档可见（`data/db` 旧库自动迁移） |
| **性能基线** | `python -m scripts.benchmark --ingest-sample` → **p50=18ms p95=25ms max=25ms，预算 P95<2s → PASS** |

> 性能结论：查询延迟远低于 2s 预算，确认 v0.2「查询保持同步」的决策正确；无需 Query 任务化（异步端点仍保留为可选）。

**容器部署**：Dockerfile / compose / .dockerignore 已交付。⚠️ **沙箱无 Docker Hub 网络**（拉取 `python:3.12-slim` 超时），构建验证留待有外网的环境执行 `make docker-build`。

## 5. 验收暴露的真实 bug（已修复）

| Bug | 根因 | 修复 |
|---|---|---|
| 旧 `data/db/ingestion_history.db`（v1，内联 `file_hash TEXT PRIMARY KEY`）→ 摄取必崩 `no such column: collection` | `_legacy_table_exists` 只匹配表级 `PRIMARY KEY (file_hash)`，漏掉内联声明；且 `_ensure_schema` 在迁移前就创建复合索引 | ① 迁移检测改为「无 `collection` 列即旧表」（两种 PK 写法都覆盖）② `_ensure_schema` 先迁移再建 v2 索引 |
| 旧 `data/db/image_index.db`（v0.2，无 `collection` 列）→ 图片查询崩 | M3 加了列但没迁移旧库（file_integrity 有迁移，image_storage 没有） | `_ensure_schema` 检测缺失列后 `ALTER TABLE ADD COLUMN collection` |

**另一个启动 bug**：`python -m src.web_api.main` 默认加载 `Settings()`（空 api_key），第一个真实请求就会因 embedding 客户端构造失败而 500。已改为默认加载 `config/settings.yaml`（存在时）。

## 6. 文档同步（已消除的过期描述）

| 文档 | 更新 |
|---|---|
| `README.md` | 新增「Web 前端与 Web API」启动段；路线图 I 行 ✅，补充 M1–M4 说明 |
| `docs/web-api-onboarding.md` | §5 端点表加 `queries/async` + `queries/{id}/result`；§8 时间表 M3 ✅（除 rerank）/ M4 ✅；补 SQLite 持久化 + 真实 health 说明 |
| `TEAM_RESPONSIBILITIES.md` | M2 批次 2/3 标记 ✅；新增 M3/M4 进度段 |
| `PRODUCTION_WEB_DEV_SPEC.md` | §8 M2/M3/M4 状态标记 ✅ |

## 7. 前端需要的最小变更

**没有强制变更**。可选：

- 总览页现在消费真实的 `providers[].ready` 与 `/system/health` 依赖列表（embedding 不可达时 `ready=false` / `status=down`，可做提示 banner）。
- 若文档详情想展示 `last_query_id`（批次 2 已加的字段），可补「最近引用该文档的查询」链接。

## 8. 验证清单

- [ ] `make api` 启动 → `/system/health` 显示真实依赖状态
- [ ] 停掉 embedding 服务 → health `embedding: down`，`status: down`（真实降级）
- [ ] `make benchmark` 跑出 p50/p95
- [ ] 旧数据目录（v1 ingestion_history / 无 collection 的 image_index）可直接启动不崩
- [ ] 新环境按 README 的 `make install` → `make dev` 完成演示流程

## 9. 反馈

- 契约 v0.2 **零变化**；快照无需重生成。
- 已知待办（M3 剩余）：**rerank 真实接入**（`enable_rerank` 仍 no-op，用户已确认暂不做）。
- **后端「除 rerank 外」已全部完成**：M1–M4 + 真实健康检查 + 发布验收。
