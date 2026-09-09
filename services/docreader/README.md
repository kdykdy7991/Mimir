# SKDY DocReader — 本地文档解析服务

> Phase 0 骨架。说明本服务与上游 WeKnora DocReader 的关系、目标结构、以及尚待实现的内容。
> 迁移计划：`docs/plan-2026-09-09-weknora-local-document-parser-migration.md`
> 上游参考：`/home/hello/workspace/WeKnora` @ `3e6010e7cd3937f289cc1dbadc829e71eb1163f4`

## 定位

独立的、本地运行的文档解析 gRPC 服务。它只负责“文件/URL 字节 → Markdown、图片字节与解析元数据”，
**不**负责文件身份、权限、图片持久化、分块、视觉增强、索引或业务库（这些仍由主服务负责，见计划 §4）。

## 为什么独立成服务（不在 Web API / MCP 进程内）

- 解析重依赖（LibreOffice、Java、Playwright、PDF 渲染、大文件）不应拖垮在线查询进程。
- 可独立扩容 / 限制并发 / 设置超时。
- 通过 `grpc ReadStream` 流式返回图片字节，避免大消息。

## 目标目录（计划 §7）

```text
services/docreader/
├── pyproject.toml
├── Dockerfile
├── README.md
├── THIRD_PARTY_NOTICES.md
├── docreader/
│   ├── main.py          # 服务入口（Phase 1 实现）
│   ├── config.py        # 服务配置（Phase 1 实现；对齐上游 docreader/config.py）
│   ├── models/          # 解析结果模型（Phase 1 迁移）
│   ├── parser/          # 解析器（Phase 1/2/3/6 迁移）
│   ├── proto/           # 生成式 gRPC stub（Phase 1 由 docreader.proto 生成）
│   └── utils/           # 工具（Phase 1 裁剪迁移）
└── tests/               # 上游回归 + 自研测试
```

## 当前状态（Phase 0）

- 已建立：`pyproject.toml`（核心 gRPC 依赖）、`Dockerfile`（基础容器）、`README.md`、
  `THIRD_PARTY_NOTICES.md`、空包结构占位。
- 未实现：服务入口、gRPC 桩、解析器、配置细节。这些在 Phase 1–3、6 逐个迁移落地，
  每个 Phase 结束都有审核材料（见 `docs/implementation-status/document-parser-migration.md`）。

## 依赖策略

Phase 0 只声明核心 gRPC 依赖（grpcio / grpcio-tools / protobuf），避免提前引入重型转换栈。
每迁移一种格式，才在其所在 Phase 追加对应依赖（见 `pyproject.toml` 注释）。

## 本地运行（待 Phase 1 entrypoint 就绪后）

```bash
cd services/docreader
uv sync          # 或 pip install -e .
python -m docreader.main --config ...   # Phase 1 提供
```

## 回滚

DocReader 是新增的独立服务；主服务通过 Feature Flag
`document_parser.backend: legacy | docreader` 切换。未完成期默认 `legacy`，可随时切回，
不影响现有上传/解析链路（计划 §1 / Phase 1 验收）。