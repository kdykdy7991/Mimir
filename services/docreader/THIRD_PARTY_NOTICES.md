# SKDY DocReader — 第三方来源与许可通告

> 本文件持续随迁移更新，逐文件记录从 WeKnora 迁移的内容、来源路径、固定提交与许可证。
> 主清单：`docs/baselines/document-parsing/provenance.md`（Phase 0 固化）。

## 上游项目

- 名称：WeKnora (DocReader 模块)
- 来源：`/home/hello/workspace/WeKnora`
- 固定提交：`3e6010e7cd3937f289cc1dbadc829e71eb1163f4`
- 根许可证：MIT — Copyright (C) 2025 Tencent
- 附带 Apache-2.0 组件（paddle-1.1.15、playwright-1.56.0、grpc-health-7.5.0）见上游
  `THIRD_PARTY_NOTICES.md` / `licenses/`。

## 待迁移文件的许可要求

1. 每个迁移文件保留其原有版权头；`docreader/utils/__init__.py` 带 **InfiniFlow Apache-2.0**
   版权头，迁移必须一并保留并在此注明。
2. Apache-2.0 许可正文在迁移该文件时随附（可从上游 `licenses/` 或 Apache 官方文本获取）。
3. 第三方 Python/Java/系统依赖的许可清单在本文件下面按“已引入”追加（Phase 1 起）。

## 已引入第三方依赖（状态：逐步填入）

| 依赖 | 用途 | 许可 | 引入 Phase |
| --- | --- | --- | --- |
| grpcio / grpcio-tools / protobuf | gRPC 服务与桩生成 | Apache-2.0 | Phase 0（核心） |
| pymupdf 见 root | PDF 文本/渲染 | AGPL-3.0（root 已引入） | Phase 2 复用 |

> 完整清单在后续 Phase 各自追加；每个格式迁移同时更新依赖许可（计划 §15）。

## 容器与运行

- 运行镜像基础（见 `Dockerfile`）：Stage 阶段仅拷贝本服务；不挂载向量库 / BM25 / 业务库（§11）。
- 容器以非 root 用户运行；只读根文件系统；受限临时目录（Phase 1 落地时验证）。