# DocReader 迁移实施进度记录

> 作用：本文件是 DocReader 迁移的**持续性进度记录**（不是事后补写）。每完成或暂停一个小任务都必须更新。
> 目标文档：`docs/plan-2026-09-09-weknora-local-document-parser-migration.md`
> 目标分支：`feature/weknora-inspired-optimizations`
> 上游参考：`/home/hello/workspace/WeKnora` @ `3e6010e7cd3937f289cc1dbadc829e71eb1163f4`

---

## 当前定位

- **当前 Phase**：Phase 0 —— 基准、来源和骨架
- **当前小任务**：建立解析基准样本集与基线指标 / 第三方来源清单 / DocReader 服务骨架
- **未完成改动**：见“工作区状态”。

---

## 进度总览

| Phase | 状态 | 说明 |
| --- | --- | --- |
| Phase 0 基准、来源和骨架 | **进行中** | 样本集、基线指标、来源清单、服务骨架 |
| Phase 1 DocReader 核心与兼容接入 | 未开始 | 依赖 Phase 0 审核通过 |
| Phase 2 WeKnora 内置 PDFParser | 未开始 | 依赖 Phase 1 审核通过 |
| Phase 3 OpenDataLoader 与表格规范化 | 未开始 | |
| Phase 4 表格感知分块 | 未开始 | |
| Phase 5 本地 Qwen3.8 27B 多模态入库 | 未开始 | |
| Phase 6 格式扩展 | 未开始 | |
| Phase 7 切换默认与清理旧实现 | 未开始 | |

---

## 关键设计决策与原因

### 决策 D0.x —— Phase 0 采用“脚本生成 + 固化结果”的样本集策略
- **决策**：解析样本（PDF / Markdown）由
  `scripts/generate_parsing_baseline_samples.py` 用 pymupdf + PIL 确定性生成，
  同时把生成产物固化到 `docs/baselines/document-parsing/samples/`，并提交生成脚本。
- **原因**：样本必须可复现、可回归、不含敏感生产资料；二进制样本虽小但要小，且必须有
  “如何再生成”的脚本作为来源。生成脚本与固定产物同时入库，保证任何工程师都能重建同样
  的固定样本（Phase 13 验收门槛要求基于 Phase 0 固定样本，不能只看观感）。
- **代价**：DOCX / XLSX / PPTX 需要 openpyxl / python-docx / python-pptx，当前 venv 未安装，
  且当前基线（legacy loader）只支持 PDF / Markdown，故这些格式在 Phase 0 只提交“期望结构
  描述符（descriptor）”，不生成二进制；对应的真实样本在 Phase 6 各格式迁移时配套补齐，避免
  在 Phase 0 引入对 Phase 6 才需要的依赖。

### 决策 D0.y —— 基线以“当前 legacy 链路”为准，不含 DocReader
- **原因**：基线反映“迁移前的事实”，应与目标迁移后的效果对比。当前链路由
  `LoaderFactory/LoaderRegistry`（PDF/Markdown）+ `RecursiveSplitter` + `DocumentChunker` 组成。
- **执行**：`scripts/run_parsing_baseline.py` 复用与生产 pipeline 相同的 loader/splitter/chunker
  构造方式（通过 `load_settings()` 读配置），对固定样本跑 load+split，记录文本长度、chunk 数、
  耗时、表格数字命中、失败类型，输出到 `docs/baselines/document-parsing/metrics/`。
- **已知**：当前 PDF Loader 不作表格检测，因此“表格关键数字准确率”基线为 0，且双栏按
  `(y,x)` 排序会交错。这些正是本迁移要改善的指标（见 Phase 2/3/4）。

### 决策 D0.z —— 进度记录本文件作为“持续更新的交接文档”
- 每个小任务在提交前更新本文件，保证下一位工程师只需读：
  目标文档 `docs/plan-2026-09-09-*` + 本进度记录 + 最近几个提交即可继续。

---

## 已完成小任务（Phase 0 内）

### T0.1 建立固定解析样本集并固化
- **内容**：新增 `scripts/generate_parsing_baseline_samples.py`，用 pymupdf + PIL 确定性生成
  PDF（单栏 / 双栏 / 有框表格 / 无框表格 / 跨页表格 / 扫描页）与 Markdown 样本到
  `docs/baselines/document-parsing/samples/`；同时把生成产物固化入库。
  新增 `docs/baselines/document-parsing/README.md` 与 `descriptors/future-formats.md`，
  描述 corpus 布局、复现方式与后续格式（DOCX/XLSX/PPTX…）的期望结构。
- **设计决策**：PDF 样本文本用 ASCII（pymupdf base-14 字体不支持 CJK 字形；CJK 由 Markdown 样本 +
  Phase 6 真实样本覆盖）；扫描页用低分辨率 JPEG 内嵌，保证样本体积小；中间掩码图写入临时目录，
  不进入 `samples/`。
- **已修改文件**：`scripts/generate_parsing_baseline_samples.py`、
  `docs/baselines/document-parsing/{README.md,descriptors/future-formats.md}` + `samples/`（7 个文件）。
- **测试/校验**：`python scripts/generate_parsing_baseline_samples.py` 成功，样本体积：1.4–21.5KB；
  `git check-ignore samples/*` 确认未被忽略（可入库）。
- **未完成**：无。
- **已知问题**：
  1. 生成 DOCX/XLSX/PPTX 二进制需 openpyxl/python-docx/python-pptx，当前 venv 未安装且无关解析基线，
     Phase 0 只提交期望结构描述符，真实样本随 Phase 6 各格式落地（已在决策 D0.x 记录）。

### T0.2 基线运行器与基线指标
- **内容**：新增 `scripts/run_parsing_baseline.py`，复用生产 load+chunk 链
  （`LoaderRegistry.from_settings` + `DocumentChunker(SplitterFactory)`），对固定样本记录
  文本长度、行数、chunk 数、load/split 耗时、表格命中、双栏标记、扫描页探测、失败类型；
  输出可 diff 的 JSON 快照到 `docs/baselines/document-parsing/metrics/baseline-2026-09-09.json`。
- **结果要点**（详见 metrics JSON）：
  - 三个表格样本的期望 ASCII 单元格字符串全部存活（hit 1.0）——但只是“文本存活”，legacy loader
    把单元格拍平成坐标排序的文本行，**不提供行列/表头结构**，因此 Phase 13 的“列数/表头保持率、
    表格关键数字准确率”当前实际不成立，是本迁移 Phase 2–4 的改进目标。
  - `two_column.pdf` 提取顺序交错（LEFT header, RIGHT para1, LEFT para1, RIGHT para2, LEFT para2,
    LEFT para3, FOOTER），是 Phase 2 多栏算法要修复的具体回归证据。
  - `scanned.pdf` 无真实文本、1 个图片占位符（`has_real_text=False`），当前无 OCR/VLM 路径。
  - 短样本在现有 splitter 下各成 1 chunk。
- **已修改文件**：`scripts/run_parsing_baseline.py`、`docs/baselines/document-parsing/metrics/baseline-2026-09-09.json`
- **测试/校验**：运行脚本成功；临时图片目录写临时区；`git diff --check` 通过。
- **未完成**：无。

### T0.3 第三方来源清单
- **内容**：新增 `docs/baselines/document-parsing/provenance.md`，按 §6 迁移矩阵逐文件记录
  WeKnora DocReader 的来源路径、固定提交、迁移方式、保留内容与需适配/删除内容；
  核实许可证：WeKnora 根为 MIT（Copyright 2025 Tencent），`docreader/utils/__init__.py`
  带 InfiniFlow Apache-2.0 头；`opendataloader_parser.py` 依赖 OpenDataLoader PDF (Apache-2.0)。
  同时列出 utils / proto / 排除项（Go client、splitter、web_parser 延后）。
- **已修改文件**：`docs/baselines/document-parsing/provenance.md`
- **测试/校验**：人工核对上游文件与提交 `3e6010e7cd3937f289cc1dbadc829e71eb1163f4`。
- **未完成**：无。

### T0.4 DocReader 独立服务骨架
- **内容**：新增 `services/docreader/`：`pyproject.toml`（核心 gRPC 依赖，格式依赖按 Phase 追加）、
  `Dockerfile`（非 root、只读 FS 意图，不设 CMD以防伪提供）、`README.md`、`THIRD_PARTY_NOTICES.md`，
  以及 `docreader/` 包占位（`main.py` 的 `serve()` 明确 NotImplementedError、`config.py`、空
  `parser/model/proto/utils` 包）。新增 `tests/test_skeleton.py` 校验包可导入、版本存在、`serve()` 未实现。
- **设计决策**：Phase 0 只声明核心 gRPC 依赖，避免提前引入重型转换栈；空解析器不放进稳定链路
  （主服务 Feature Flag 默认 `legacy`，可随时切回）。
- **已修改文件**：`services/docreader/**`
- **测试/校验**：`PYTHONPATH=services/docreader pytest services/docreader/tests` → 3 passed；
  `pyproject.toml` 经 tomllib 解析 OK。
- **未完成**：真实 gRPC entrypoint（Phase 1）、解析器（Phase 1/2/3/6）。

### T0.0 调研并建立进度记录
- **内容**：通读目标文档；确认当前分支 `feature/weknora-inspired-optimizations` 干净；
  确认上游 `/home/hello/workspace/WeKnora` 处于固定提交 `3e6010e7cd3937f289cc1dbadc829e71eb1163f4`；
  确认当前差异证据与文档 §2.1 一致：
  - `loader_factory.py` 只注册 `.pdf/.md/.markdown`；
  - `web_api/settings.py` 上传白名单 MIME 含 `text/plain` 但扩展名无 `.txt`（配置不一致，记录到已知问题）；
  - `pdf_loader.py` 按 `(y_top, x_left)` 排序文本与图片（无法表达双栏阅读顺序与表格行列）；
  - `pdf_loader.py` 未调用 `find_tables()`；
  - 模型配置 `config/settings.yaml:13` 为 `Qwen3.6-35B-A3B-NVFP4`，与实际本地 Qwen3.8 27B 不一致（已记录）。
- **已修改文件**：本进度记录 `docs/implementation-status/document-parser-migration.md`
- **测试**：`pytest tests/unit/test_loader_pdf_contract.py test_loader_markdown_contract.py
  test_loader_factory.py test_document_chunker.py test_core_types.py` → 115 passed。
- **未完成**：样本集、基线指标、来源清单、DocReader 骨架。
- **已知问题**：
  1. `web_api/settings.py` 白名单 MIME/扩展名不一致（`.txt`），Phase 6 处理 TXT 时一并修复。
  2. 全量 `tests/unit` 有 16 个**既有**失败，与解析迁移无关，为本分支基线既有状态：
     主要是测试环境的 `socks://127.0.0.1:7890/` 代理导致 LLM provider 实例化失败（
     `test_llm_providers_smoke`、`test_multimodal_llm` 等），以及若干 prompt 文件相关断言差异
     （`test_chunk_refiner`、`test_image_captioner_fallback`、`test_metadata_enricher_contract`）。
     已在基线指标文档中记录，作为回归基线；后续提交不得使其新增失败。

---

## 迁移来源映射（WeKnora → 本项目）

> 详见 `docs/baselines/document-parsing/provenance.md`。逐文件记录来源路径、固定提交、迁移方式、
> 保留内容与必须适配内容。

---

## 已运行命令与结果

> 本文件按小任务持续追加；下表为最近一次的关键命令。

| 命令 | 结果 |
| --- | --- |
| `git status` | 提交前干净，见“工作区状态” |
| `pytest tests/unit/test_loader_pdf_contract.py ...` | 115 passed |

---

## 未完成小任务（Phase 0 内，按应做顺序）

- [x] T0.0 调研并建立进度记录
- [x] T0.1 生成固定解析样本集并固化
- [x] T0.2 编写基线运行器与基线指标
- [x] T0.3 编写第三方来源清单
- [x] T0.4 创建 `services/docreader/` 独立服务骨架
- [ ] T0.5 汇总 Phase 0 审核材料（提交列表、变更、迁移映射、测试命令、已知限制、回滚、下一阶段建议）
      —— 以本进度记录下方的 Phase 0 审核交接节呈现，提交后暂停等待审核。

---

## 已知问题清单

1. `web_api/settings.py` 上传白名单 MIME/扩展名不一致（含 `text/plain` 无 `.txt`）。计划 Phase 6 修复。
2. `config/settings.yaml` 视觉模型名 `Qwen3.6-35B-A3B-NVFP4` 与实际本地 Qwen3.8 27B 不一致。计划 Phase 5 统一。
3. 全量 `tests/unit` 16 个既有失败（见 T0.0），与本迁移无关，作为基线记录。
4. 当前 PDF Loader 无表格检测、无双栏阅读顺序恢复。这是本迁移的核心改进点（Phase 2/3/4）。

---

## 工作区状态

（每次提交后更新）

- 未提交改动：初期建立进度记录文件后即提交；此后以 `git status` 为准。
- 提交策略：遵循规范——小提交、每提交解决一个问题、`git diff --check` 校验、不在无谓文件上重写。