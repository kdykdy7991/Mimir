# DocReader 迁移实施进度记录

> 作用：本文件是 DocReader 迁移的**持续性进度记录**（不是事后补写）。每完成或暂停一个小任务都必须更新。
> 目标文档：`docs/plan-2026-09-09-weknora-local-document-parser-migration.md`
> 目标分支：`feature/weknora-inspired-optimizations`
> 上游参考：`/home/hello/workspace/WeKnora` @ `3e6010e7cd3937f289cc1dbadc829e71eb1163f4`

---

## 当前定位

- **当前状态**：**第三轮审核整改中（部署闭环批次）**。上一轮（Phase 5–7 rework）已按审核意见逐项整改并通过
  大部分复核，但第三轮复审仍指出 3 个阻断项 + 2 个一般问题（见下方「第三轮审核意见整改记录」）：DocReader
  容器无入口/无 PDF 运行依赖、启动 fail-fast 实为惰性 channel、指标 README 与快照不一致、CLI 白名单未随
  Web 同步。本轮针对这些逐项补成部署闭环（容器入口/health/启动探活、镜像内 PyMuPDF、真实镜像 PDF gRPC
  烟测、指标报告由脚本生成、CLI 从统一上传白名单派生）。
- **未完成改动**：见“工作区状态”。

---

## 第三轮审核意见整改记录（部署闭环批次）

> 依据第三轮复审（上一轮整改方向基本正确，但仍有 3 阻断 + 2 一般）。默认 backend 保持 legacy（代码）、
> 生产 config/settings.yaml=docreader 不变；不推送、不删除旧 Loader；每项独立小提交 + 独立验证。

| # | 审核问题 | 整改 | 状态 |
| --- | --- | --- | --- |
| 阻断1 | `services/docreader/Dockerfile` 无 CMD，仍保留“尚未提供服务入口”骨架注释；compose 解析 command/entrypoint 均 null，容器起来即反复重启 | 补 `CMD ["python", "-m", "docreader.main"]`（直接跑 Python，信号直达进程）；删陈旧骨架注释；Dockerfile 加 `HEALTHCHECK`（`docreader.health_check`），新增 `services/docreader/docreader/health_check.py`：等 channel READY + 真实 ListEngines + builtin engine 覆盖最低格式；docker-compose docreader 加 healthcheck、api `depends_on` 改为 `service_healthy`。 | 完成 `a8b97e5` |
| 阻断2 | “启动期 fail-fast”实未实现：`grpc_transport.py` 仅 `grpc.insecure_channel(endpoint)` 构造惰性 channel，DocReader 不存在时 pipeline 构建仍成功，错误延迟到首次上传解析 | `DocReaderGrpcTransport.probe()`：`channel_ready_future().result(timeout)` + 真实 ListEngines + builtin engine 可用且覆盖 `MIN_READY_FORMATS`（pdf/md/markdown/txt/csv/docx/xlsx/pptx/epub/xmind/html；DOC/XLS/PPT 门控占位与 opendataloader 故意不含）。`build_document_parser_from_settings` 在构造真实 transport 后即调用 `probe()`，失败抛 `EngineUnavailableError`（启动期暴露，非首次解析）。 | 完成 `a8b97e5` |
| 阻断3 | DocReader 镜像缺 PDF 运行依赖：`pyproject.toml` 只有 gRPC/protobuf；Dockerfile 只装本包；105 测试在宿主 .venv 跑，不能证明镜像内 PDF 可解析 | `pyproject.toml` 补 `pymupdf`（PDF builtin 后端）+ `pillow`（图片 magic 校验），镜像运行时即含 PDF 依赖；新增 `scripts/smoke_docreader_container.py` 真实镜像烟测：build→start→health ready→gRPC ReadStream 真 PDF→返回非空 Markdown/扫描页图片帧。 | 代码就绪；镜像烟测见下方命令/日志 |
| 一般4 | 指标 README 与 JSON 快照不一致：快照 bordered/borderless/cross-page 各 174/176/545 字符，README 却写 116/118/392（行数 9/9/22 vs 实际 19/19/47）；建议比较报告由脚本生成 | 新增 `scripts/render_parsing_metrics_report.py`：直接从 baseline/after 两个 JSON 计算 delta 表并产出 `comparison.md`；修正 README 文本形状表为 174/176/545（行数 19/19/47），标注旧草稿不一致；README 引用脚本生成产物与再生成命令。 | 完成 `41311e4` |
| 一般5 | CLI `scripts/ingest.py SUPPORTED_EXTS` 只有 pdf/md/markdown，无法枚举 DOCX/XLSX/PPTX/EPUB/XMind 等（Web 已含）；两套白名单会漂移 | 统一上传格式白名单收敛为单一来源 `src/application/services/upload_types.py::DEFAULT_UPLOAD_ALLOWED_EXTENSIONS/_MIME`；`WebAPISettings` 默认值导入它；`scripts/ingest.SUPPORTED_EXTS = tuple(sorted(...))` 派生。测试断言 CLI 白名单 == 规范化白名单且含 docreader 各格式。 | 完成 `5ea0ec9` |

**本轮状态与验证**：相关单元/集成测试均绿（docreader 109、主工程相关单测、e2e test_data_ingestion 21、
web_api 依赖 12）；`git diff --check` 干净；迁移后指标 hit_ratio=1.0（raw-text 存活，非表格结构准确率，README 已如此表述）。

---

## 审核意见整改记录（Phase 5–7 rework）

> 依据一次独立审核（结论：不能视为 Phase 0→7 已交付）。以下按审核问题逐项记录整改与状态。整改期间默认
> backend 保持 legacy（代码），生产配置 config/settings.yaml=docreader 不变；不推送、不删除旧 Loader。

| # | 审核问题 | 整改 | 状态 |
| --- | --- | --- | --- |
| 1 | DocReader 未接入正式业务入库链路（CLI/Web/MCP/Dashboard） | `resolve_document_parser` 统一路由；CLI `scripts/ingest.py main()`、Web `EngineCache._build_pipeline`、Streamlit Dashboard `ingestion_service._build_pipeline` 全部创建并传入 docreader parser（MCP 无 ingest pipeline）。 | 完成 `108879d` |
| 1b | 异常静默回退 legacy，违背“错误启动期暴露” | 改 fail-fast：backend=docreader 且 transport 构建失败即抛错；代码默认改回 legacy（程序化/测试安全），生产默认仍由 yaml=docreader 决定。 | 完成 `108879d` |
| 2 | 多模态 VisionIngestTransform 未进生产 pipeline | docreader 路径+LLM+视觉开关下装配进 transforms（取代旧 captioner），补齐 OCR/Caption 子分块链路。 | 完成 `1d9b854` |
| 3 | DocReader 图片字节被丢弃（adapters path=filename，无 image_persistence） | ImageRef 增 `data`/`mime_type`；适配器携带字节；`IngestionPipeline._register_images` 落盘 ImageStorage 并把 path 改写为真实文件、分块前清除内联字节。 | 完成 `4c56bc5` |
| 4 | ZIP 类格式缺安全限制（zip bomb） | `zip_safe.py` 四重限制（数量/单文件/总大小/压缩比）接入 DOCX/XLSX/PPTX/EPUB/XMind。 | 完成 `53037ab` |
| 5 | DOC/XLS/PPT 实际不可用却列为“已交付” | **修正为门控占位**（非交付）：`legacy_office_parser` 不可用时明确拒绝、绝无假成功；本环境无 OLE2 转换器；部署端装转换器后再启用。已在本文件 Phase 6 表格/D6.4 更正在案。 | 完成 `427fb02` |
| 6 | Phase 6 为精简重实现，缺阅读顺序/结构保真 | OOXML/EPUB 保真增强：EPUB spine 阅读序；XLSX 工作表顺序/名称/合并单元格/布尔/公式缓存值；DOCX 标题层级/粗斜体/超链接/合并单元格；PPTX 表格+备注。新增 5 测试。 | 完成 `c5c2bc8`,`894061d`,`f450095`,`7db18dc` |
| 7 | Phase 7 前置（迁移后指标快照、真实业务灰度） | 迁移后指标快照 + 基线对比已产出（`after-2026-09-10.json`，真实执行，已复测 hit_ratio=1.0）；真实业务灰度需部署。 | 快照完成；灰度待部署 |
| 7b | （快照发现）PDF 布局重建丢弃表格数字单元格 | 根因在 `pdf_postprocess.strip_chart_text_debris` 过度清洗纯数字列（chart 刻度误判）；收紧判定（须多 token 才算刻度），纯数字单元格保留。freefixtures 复测 hit_ratio=1.0。 | 完成 `58a7bd8` |
| 8 | docker compose 默认不启 docreader、注释与实际不符 | docreader 改为 compose 默认服务并 `depends_on` api；删旧“默认 legacy”注释。 | 完成 `b57e76e` |
| 9 | 配置模型非 Qwen3.8-27B、保留多套 VLM 能力探测名单 | config `llm.model=Qwen3.8-27B`；`LLMSettings.supports_vision`（默认 True）取代 `VISION_MODELS` 名单驱动 capabilities。 | 完成 `7f5612e` |

**迁移后快照发现并已修复的真实缺陷**：builtin PDF 布局重建一度丢弃表格数字单元格（bordered/borderless/
cross_page 三例 hit_ratio 1.0→~0.6），raw pymupdf 文本本含这些值。根因在 `pdf_postprocess`
`strip_chart_text_debris` 把连续纯数字列误判为图表刻度而整体清除；已将该判定收紧为“须多 token 的刻度行”，
纯数字单元格保留。修复后三例 hit_ratio 复测 = 1.0（见 `58a7bd8`），迁移后快照也据此回正。

---

## 进度总览

| Phase | 状态 | 说明 |
| --- | --- | --- |
| Phase 0 基准、来源和骨架 | **完成（已审核）** | 样本集、基线指标、来源清单、服务骨架 |
| Phase 1 DocReader 核心与兼容接入 | **完成（已审核）** | 契约/客户端/flag/pipeline + 服务端核心+gRPC+部署探活 |
| Phase 2 WeKnora 内置 PDFParser | **完成（已审核）** | pymupdf 后端：分类/layout/去噪/扫描渲染/嵌入式图 |
| Phase 3 OpenDataLoader 与表格规范化 | **完成（与 Phase 4 同时交付）** | 引擎+规范化+路径卫生+表格感知分块 |
| Phase 4 表格感知分块 | **完成（与 Phase 3 同时交付）** | 保护 span、原子表、行级拆分补表头 context_header |
| Phase 5 本地 Qwen3.8 27B 多模态入库 | **完成（审核待批）** | 子分块生产器+摄入接线+失败语义+索引路由 |
| Phase 6 格式扩展 | **整改中（保真增强已交付，待整体复核）** | DOCX/CSV/XLSX/PPTX/HTML/MHTML/EPUB/XMind/图片 已交付；legacy DOC/XLS/PPT 为**门控占位（非交付）**；OOXML/EPUB 保真增强完成（EPUB spine/XLSX 顺序/合并/公式缓存/DOCX 层级/粗斜体/超链接/合并单元格/PPTX 表格/备注） |
| Phase 7 切换默认与清理旧实现 | **进行中**（P7.1 已提交：切换默认+回滚开关） | 删除旧 Loader 留待无回滚后 |

---

## Phase 2 进度（进行中）

> 说明：上游 `docreader/parser/pdf_parser.py`（1618 行）深度依赖 pypdfium2，而本项目 PDF 栈是
> pymupdf（§10）。因此 Phase 2 是**重点迁移 + 后端改写**：保留上游算法（逐页分类、XY-cut 多栏、
> 标题/噪声清理、扫描页渲染、嵌入式图/图表区），把 pdfium 底层原语改写为 pymupdf 等价物。
> 按小任务逐个提交，每个带测试；测试在 services/docreader 内用合成 PDF（pymupdf+PIL）驱动。

### T2.1 feat(docreader): port pdf page classification (pymupdf backend)
- **内容**：新增 `services/docreader/docreader/parser/pdf_classify.py` —— 纯函数 `classify_page`
  （**直接迁移**自上游 `_classify_page`，主信号为图片面积占比、次信号为低文本+有图）+
  pymupdf 后端 `page_image_area_ratio` / `page_text`。阈值常量以 env 覆盖（同上游）。
- **测试**：`services/docreader/tests/test_pdf_classify.py` → 5 passed（纯规则 + 合成扫描页/文本页）。

### T2.2 feat(docreader): port layout-aware multi-column pdf text over pymupdf
- **内容**：新增 `services/docreader/docreader/parser/pdf_layout.py` —— 上游 XY-cut 多栏/阅读顺序
  算法**逐字迁移**（find_split/split_columns/article-column 过滤/heading 提升/plain-vs-layout 回退）；
  仅 `page_chars` 改写：pymupdf `rawdict` 取字级 bbox，并把 y 从 pymupdf 顶左原点翻转为 pdfium 的
  **底左原点**，使迁移算法坐标语义一致。
- **测试**：`test_pdf_layout.py` → 3 passed（合成双栏 PDF 验证按列线性化、非行交错；纯列拆分丢弃窄边栏）。

### T2.3 feat(docreader): port pdf text post-processing (noise cleanup)
- **内容**：新增 `services/docreader/docreader/parser/pdf_postprocess.py` —— 上游文本净化逐字迁移：
  sanitize 占位符/断字、去 arXiv/页号行、去除图表轴标碎屑、清理 Figure 标题上方标签行。
- **测试**：`test_pdf_postprocess.py` → 5 passed。

### T2.4 feat(docreader): port routed pdf parser (text/scanned/hybrid) over pymupdf
- **内容**：新增 `services/docreader/docreader/parser/pdf_parser.py` —— 上游 PDFParser 逐页路由算法迁移：
  文本页 layout/reconstruction + 后处理，扫描页渲图为 JPEG（`render_page_to_jpeg`，pymupdf pixmap，
  按 max_edge 钳制长边、按 jpeg_quality 编码），`strip_repeating_lines` 去跨页页眉/页脚，
  嵌入式图提取（`extract_embedded_images`：xref 去重、尺寸/面积过滤、跨页重复去 logo/水印、总量上限），
  markdown 按阅读顺序装配 + 元数据。`models/Document.images` 存**原始字节**（上游存 base64；本项目
  main.py 直接把 image_data 喂 gRPC proto，不做 base64）。`config.py` 新增 pdf_render_*
  /pdf_jpeg_quality 旋钮（启动期校验 §9）。`registry.py` 注册 pdf 引擎。
- **测试**：`test_pdf_parser.py`（文本/扫描/混合逐页路由/嵌入式图/force_scanned 覆盖）+ registry +
  gRPC 真 PDF 走线 → services/docreader 全量 48 passed。
- **基线回归**（docs/baselines/…/samples）：two_column 正确按列线性化（LEFT-C 全部先于 RIGHT-C）、
  scanned 路由为扫描页且渲染页面图、bordered/borderless/cross_page/single_column 均正确走文本层。
- **malformed**：非 PDF / 空字节抛干净异常（FileDataError/EmptyFileError），由服务端包成 error。

### T2.x 说明与偏差
- 矢量图区（chart region）渲染本轮**关闭**（`RENDER_VECTOR_FIGURES` 默认 0），避免产出不完整图表；
  其 clip 检测/注入留作后续小任务（对应上游 `_extract_vector_figure_clips`）。
- 隐藏文本（render-mode 3 / invisible box）过滤本轮未含（pdfium 特有 API）；留作后续清理小任务。
- 扫描页渲染当前进程内串行（未用多进程 `pdf_render_parallelism`）。

### 未完成
- Phase 2 审核交接（T2.6）见下。

## Phase 3 进度（进行中）

> 计划 §Phase-3：接入本地 OpenDataLoader（引擎 `opendataloader`），禁止非本机/白名单外地址，普通表格
> 规范化 GFM，带 rowspan/colspan 且无法无损转换的保留 HTML（去样式），稳定独立块边界（不泄漏临时路径/
> base64），过短/失败回退 builtin PDFParser 并记录尝试链。注意：计划「表格解析与表格感知分块必须同时交付」，
> Phase 4 分块是本阶段验收的一部分。

### T3.1 feat(docreader): add table/markdown normalization (gfm, clean html, path hygiene)
- **内容**：`services/docreader/docreader/parser/table_normalize.py` —— GFM 分隔行规范化；带
  `rowspan/colspan` 的 HTML 表格按需保留并剥离 style/class/border 等展示属性（HTMLParser 白名单）；
  绝对/临时路径图片引用重写为 `images/<basename>`（不泄漏本机路径，不含 base64）。
- **测试**：`test_table_normalize.py` → 5 passed。

### T3.2 feat(docreader): add local opendataloader engine, registry routing and scanned fallback
- **内容**：`parser/opendataloader_parser.py` —— 上游**裁剪迁移**：仅本地（去掉 Hybrid/远程/SSRF）；可用性
  探针 `opendataloader_available`（Java + `opendataloader-pdf` 包）；临时目录 convert、图片收集
  （**原始字节**）、markdown 图引用改写、输出过短回退 `PDFScannedParser`、经 `normalize_markdown` 净化。
  新增 `PDFScannedParser`（全部页面渲图）。`config.py` 增 odl_max_workers / odl_markdown_with_html。
  `registry.py` 重构为 **(fmt, name) 复合键**，支持同格式多引擎：pdf → builtin / opendataloader；
  `parse_file(parser_engine=...)` 显式选引擎；`list_engines` 上报 opendataloader 可用性。
- **测试**：`test_opendataloader.py` + registry → 59 passed（mock JVM 包装包）。本环境无
  `opendataloader-pdf`（离线无 pip），真实 convert 未运行；引擎以 available=False + 原因上报。

### 未完成
- Phase 4 表格感知分块（主工程 DocumentChunker/Chunk 契约扩展，计划「同时交付」）。

## Phase 4 进度（完成）

> 计划 §Phase-4：识别 GFM/HTML 表为 protected spans，分块边界不得落在表内；小表原子化；大表按完整行拆、
> 后续块自动补表头（context_header）；表头只进 metadata 不破坏正文 offset；最大保护长度 7500。

### T4.1 feat(chunking): add table-aware chunking with protected spans and context headers
- **内容**：`src/ingestion/chunking/table_protection.py` —— GFM 表（页眉行+分隔行+数据行）与完整 HTML
  `<table>` 的 protected span 扫描（阅读顺序、重叠合并、严格前进避免单行 HTML 死循环）；`split_table`
  原子化或按完整行拆分（GFM 每块重复表头；HTML 保留 thead 行）。`document_chunker.py` —— 无表文档走原
  `_split_plain`（完全等价，旧测试不变）；有表文档走表感知路径，`_build_chunk`/`_inherit_metadata` 增
  `extra_meta` 合并。分块元数据：`content_type=table`、`table_index`、`context_header`（拆分时）、
  `table_part_index`（多块时）；补充表头只在 metadata 中，不改变正文文本与 offset。
- **测试**：`test_table_aware_chunking.py` → 7 passed；`test_document_chunker.py` → 29 passed（无回归）；
  首轮发现并修复：GFM 末行丢失（off-by-two）、单行 HTML 表 `index_at` 不前进导致死循环。

## Phase 5 进度（进行中）

> 计划 §Phase-5：本地 Qwen3.8 27B 多模态入库；触发条件（scanned_pdf / 内容图 / 文本空或质量低 /
> 图片表格 / force_vision）；每图 OCR+Caption → image_ocr / image_caption 子 Chunk；单图失败仅告警继续、
> 全部失败则文档任务失败，**不得把空占位符标记为成功**；保留 supports_vision / 模型路由 / 文本降级结构；
> 不实现外部付费 VLM。现有 `src/libs/llm`（capability_validator / ContentBlock / VISION_MODELS）复用之。

### T5.1 feat(vision): add multimodal ocr/caption sub-chunk producer with capability gate
- **内容**：`src/document_parser/vision/multimodal_ingest.py` —— 复用 LLM capability 模型做视觉门
  （`vision_supported`），`supports_vision_probe` 一次真实 1×1 PNG 探测校验；OCR/Caption 提示词构造；
  拒答/提示回显/空/无效 OCR 清理（`cleanup_vision_response`/`_echo_likely`）；`produce_vision_subchunks`
  产出 image_ocr / image_caption `VisionSubChunk`（含 parent 文档/分块、页码、图片 ID、模型版本）。
  单图失败 skip 并告警、非视觉模型 skip，绝不当成功。
- **测试**：`test_vision_ingest.py` → 7 passed（能力门、拒答/回显/空检测、真实图探测、子分块生成、拒答 skip）。

### T5.2 feat(vision): wire vision-ingest transform with triggers and failure semantics
- **内容**：`src/ingestion/transform/vision_ingest_transform.py` —— 相位 5 触发条件（force_vision / scanned_pdf /
  内容图 / 图片表格 / 文本过短(默认 <30 字)）；对齐 T5.1 生产器，给每个内容图生成 image_ocr / image_caption
  子分块并写入 `vision_subchunks` metadata；失败语义：单图失败仅告警、不当作成功；扫描文档全部视觉解析失败
  → 抛错使任务失败、绝不把空占位符标记为成功；数字文档附属图失败→ `has_unprocessed_images`（partial_success）。
  默认 `enabled=False`（feature flagged，不改变现有摄入行为）；producer/加载器可注入便于单测。
  `openai_compatible.py` `VISION_MODELS` 增补本地 `Qwen3.8-27B`（模型名一致性；能力全集仍 exact-string）。
- **测试**：`test_vision_ingest_transform.py` → 7 passed（触发条件、disabled passthrough、子分块元数据、
  装饰图跳过、扫描全失败抛错、扫描部分成功、数字部分成功）。

### T5.3 feat(vision): route ocr/caption sub-chunks through embedding, bm25 and vector index
- **内容**：`vision_ingest_transform.py` 将每个产出的 image_ocr / image_caption `VisionSubChunk` 展开成独立的
  `Chunk`（`is_vision_subchunk`、`chunk_type`、`parent_chunk_id`、`content_type`、图片 ID、模型版本），
  追加到分块列表后与原 chunk 一并进入 dense Embedding、稀疏 BM25 与向量索引（复用 batch_processor/upsert）。
  父 chunk 仍保留 `vision_subchunks` metadata 供追踪。
- **测试**：新增子分块被展开为 3 个可索引 Chunk（父 + ocr + caption）断言；全量相关 108 passed、
  batch_processor/ingestion_service 29 passed，无回归。

## Phase 6 进度（进行中，逐格式交付）
> 计划 §Phase-6 顺序：DOCX → XLSX/CSV → PPTX → DOC/XLS/PPT → TXT/HTML/MHTML → EPUB/XMind → 图片。
> 每格式必须同时交付：解析器、依赖、格式路由、上传白名单、Magic/MIME 校验、测试样本、验收测试；
> 禁止一次 PR 混入全部格式。Phase 0 上传/解析基准相应扩展。

### D6.1 DOCX（交付，待审）
- **解析器**：`docreader/parser/docx_parser.py` —— 依赖自由（stdlib `zipfile`+`xml.etree`，no python-docx）；
  读 `word/document.xml`，段落 `<w:p>` → 文本、表格 `<w:tbl>` → GFM Markdown（含表头/分隔行），保持阅读顺序。
- **格式路由**：registry 注册 `docx`→`builtin`（`parse_file`/`list_engines` 含 docx）。
- **上传白名单/MIME**：`web_api/settings.py` 与 `application/services/upload_types.py` 的
  allowed_extension 增 `.docx`、allowed_mime 增
  `application/vnd.openxmlformats-officedocument.wordprocessingml.document`。
- **Magic 校验**：解包确认为 ZIP 且含 `word/document.xml`，否则 ValueError（无效 docx 拒绝）。
- **测试样本/验收**：`test_docx_parser.py` 内存生成样本（段落+表格）→ 4 passed；全量 docreader 63 passed；
  上传相关 batch/web_api/collections 25 passed。
- **依赖**：无新增（stdlib only），离线环境无需 pip。

### D6.2 CSV / XLSX（交付，待审）
- **解析器**：`csv_parser.py`（stdlib `csv`，UTF-8-sig + latin-1 回退；行→GFM 表，管道转义）与
  `xlsx_parser.py`（stdlib `zipfile`+`xml.etree`；shared string / inlineStr / 数值单元格按行列排布，多
  sheet 与空行分块各成一张 GFM 表）。均依赖自由。
- **路由/白名单/MIME**：registry 注册 `csv`/`xlsx`→`builtin`；白名单增 `.csv`/`.xlsx` 与
  `text/csv`/`application/csv`/`...spreadsheetml.sheet`。
- **Magic 校验**：xlsx 非 ZIP 拒绝；含 worksheet/sharedStrings 才解析。
- **测试样本/验收**：`test_csv_xlsx_parser.py` 内存样本（CSV；XLSX shared/inline/数值）→ 5 passed；
  全量 docreader 68 passed、上传相关 25 passed。
- **依赖**：无新增（stdlib only）。

### D6.3 PPTX（交付，待审)
- **解析器**：`pptx_parser.py`（stdlib `zipfile`+`xml.etree`；按序解析 `ppt/slides/slideN.xml`，提取各 shape 的
  `<a:p>`/`<a:t>` 文本，每 slide 一个 `<!-- slide N -->` 块）。依赖自由。
- **路由/白名单/MIME**：registry 注册 `pptx`→`builtin`；白名单增 `.pptx` 与 `...presentationml.presentation`。
- **Magic 校验**：非 ZIP/无 `ppt/slides` 拒绝。
- **测试样本/验收**：`test_pptx_parser.py` 内存样本（2 slides 多 bullet、顺序断言）→ 3 passed；
  全量 docreader 71 passed、上传相关 25 passed。
- **依赖**：无新增（stdlib only）。

### D6.4 legacy DOC/XLS/PPT（门控占位，非交付）
- **解析器**：`legacy_office_parser.py` —— OLE2 二进制、本环境无本地转换器（无 LibreOffice/catdoc/antiword、
  python-docx/openpyxl/xlrd/olefile），按计划「无专有依赖则走 opendataloader 型后端」做**可用性门控**：
  注册 `doc/xls/ppt`→`opendataloader` 引擎，`available=False` + 明确 reason 上报；`parse` 在转换器缺失时
  抛可读错误，**绝不返回空占位当作成功**。
- **路由/白名单/MIME**：registry 注册 doc/xls/ppt；白名单增 `.doc/.xls/.ppt` 与 `application/msword`、
  `application/vnd.ms-excel`、`application/vnd.ms-powerpoint`。
- **测试样本/验收**：`test_legacy_office_parser.py` → 3 passed（可用性上报、三格式注册、缺失时拒绝对三种
  扩展名/route 均抛错）。
- **依赖**：无新增（运行时返回不可用，部署端装本地转换器即可启用）。

### D6.5 TXT/HTML/MHTML（交付，待审）
- **解析器**：TXT 复用既有 `PlainTextParser`；`html_parser.py`（stdlib HTMLParser，标题 `#` 前缀、去 script/style、
  单元格行内处理）；`mhtml_parser.py`（stdlib `email`，取首个 text/html|text/plain part）。
- **路由/白名单/MIME**：registry 注册 html/htm/mhtml/mht；白名单增 `.txt/.html/.htm/.mhtml/.mht` 与 `text/html`；
  批处理「不支持的扩展」验收样本由 `.txt` 改为 `.xyz`（因 .txt 现已支持）。
- **测试样本/验收**：`test_html_mhtml_parser.py` → 5 passed；全量 docreader 79 passed、上传相关 25 passed。

### D6.6 EPUB/XMind（交付，待审）
- **解析器**：`epub_parser.py`（stdlib zipfile，取全部 .xhtml/.html part 去标签按序拼接）；`xmind_parser.py`
  （stdlib zipfile，读 content.json 或旧 content.xml，递归抽 topic 标题按层级缩进）。
- **路由/白名单/MIME**：registry 注册 epub/xmind；白名单增 `.epub/.xmind` 与 `application/epub+zip`、
  `application/xmind`。
- **测试样本/验收**：`test_epub_xmind_parser.py` → 5 passed；全量 docreader 84 passed、上传相关 25 passed。

### D6.7 图片格式（交付，待审）
- **解析器**：`image_parser.py` —— 用 Pillow 做真实魔法/解码校验（坏图拒绝），无固有文本；原始字节写入
  `Document.images`（与项目 ImageRef 规则一致）供 Phase-5 视觉流水线 OCR/Caption。
- **路由/白名单/MIME**：registry 注册 png/jpg/jpeg/gif/webp/bmp；白名单增对应扩展与 `image/*` MIME。
- **测试样本/验收**：`test_image_parser.py` → 3 passed；全量 docreader 87 passed、上传相关 25 passed。

## Phase 6 审核交接（D6.complete）
> 相位 6 全部 7 类格式交付完毕（DOCX → XLSX/CSV → PPTX → DOC/XLS/PPT → TXT/HTML/MHTML → EPUB/XMind → 图片），
> 依赖自由（stdlib；图片已验证的 Pillow），逐格式同时交付解析器/路由/白名单/MIME/魔法校验/样本/验收，多小提交。

### 1) 提交列表
| 提交 | 内容 |
| --- | --- |
| `6948742` / `d33d191` | D6.1 DOCX 解析+路由 / 白名单+MIME |
| `02c163a` / `6aedaac` | D6.2 CSV+XLSX 解析+路由 / 白名单+MIME |
| `d3db1c8` / `50470d5` | D6.3 PPTX 解析+路由 / 白名单+MIME |
| `6354fc1` / `0762c4b` | D6.4 legacy DOC/XLS/PPT 门控后端 / 白名单+MIME |
| `1eff9ac` / `ef3dc60` | D6.5 HTML/MHTML 解析+路由 / 白名单+MIME+拒收样例修正 |
| `7842879` / `4ae31ff` | D6.6 EPUB/XMind 解析+路由 / 白名单+MIME |
| `ecdb04c` / `19131dc` | D6.7 图片解析+路由 / 白名单+MIME |

### 2) 完成内容
- 七类格式全部：解析器（stdlib / Pillow）、格式路由（registry 多格式别名）、上传白名单+MIME（web_api 与
  服务双处）、魔法校验（ZIP 结构 / tag / 图像解码）、内存生成测试样本、验收测试。
- 保持「无专有依赖」原则：DOCX/XLSX/PPTX/CSV/HTML/MHTML/EPUB/XMind 均 stdlib；图片用 Pillow；legacy
  DOC/XLS/PPT 以可用性门控后端交付（不可用时拒绝对报错，绝不假装成功）。

### 3) 测试与结果
- services/docreader 全量 87 passed；主工程上传/批处理相关 25 passed；`git diff --check` 干净。

### 4) 已知限制
- legacy DOC/XLS/PPT 仍为不可用后端（本环境无本地 OLE2 转换器）；部署端装本地转换器后即启用。
- MHTML 仅取首个 text/html（或 text/plain）part；EPUB 未按 spine 精确排序（按文件名序）。
- 主侧摄入对新格式的端到端灰度（经 docreader 服务）属部署期验证。

### 5) 下一阶段建议
- 进入 **Phase 7**（切换默认与清理旧实现）：前置条件——Phase 0 基准全部达门槛且至少一次真实业务文档灰度。
  建议：先在部署端跑一次真实 docx/xlsx 等灰度，再切换默认 backend 为 `docreader`、保留 legacy 回滚开关、清理
  重复的 PDF/Markdown Loader（迁移其图像分类与 metadata 合同）、更新 OpenAPI/README/运维检查表。

---

## Phase 6 → 7 本地灰度演练
> 作为 Phase 7「至少一次真实业务文档灰度」的本地前置演练（备选：部署端真实灰度仍需一次）。

### 证据（a007c73 已提交 `test_gray_run_rehearsal.py`，in-process 真实 gRPC）
- 用真实 registry 起 DocReader 服务（所有已注册引擎），经生成 stub 走读检查 `ListEngines` 上报
  `builtin`（含全部 Phase-6 格式）与 `opendataloader`（doc/xls/ppt 门控 unavailable+reason）。
- 真实 `ReadStream` 解析 md / docx / pdf → 均 `SUCCESS` 且命中预期文本。
- 另在会话中手工起了独立端口 real 服务，用主侧 `DocReaderClient`（真正 gRPC transport）连上，md/docx/pdf
  全部解析成功 —— 证明主侧「docreader backend ↔ 独立服务」全链路可用。
- `services/docreader/tests` 全量 88 passed（含该演练），工作区干净。

### Phase 7 就绪结论
- 默认 `backend=legacy` 已全程保留，分支始终可运行；切换 `docreader` 的代码路径已在上述灰度演练中验证可通。
- 部署前仍建议做一次真实业务文档灰度（本环境离线，仅本地演练）再执行默认切换。

## Phase 7 进度（进行中）
> 计划 §Phase-7：①默认 backend 改 docreader；②保留 legacy 回滚开关≥一个发布周期；③确认无回滚后再删除重复
> PDF/Markdown Loader；④删除前迁移其独有图片分类与 metadata 合同；⑤更新 OpenAPI/README/部署文档/运维检查表。

### P7.1 feat(config): switch default parser backend to docreader with legacy env rollback
- **内容**：`src/core/settings.py` 默认 `backend=legacy→docreader`；`load_settings` 增环境变量
  `DOCUMENT_PARSER_BACKEND=legacy` 作为回滚开关（无需改代码/数据迁移）；`config/settings.yaml` 默认改
  `docreader` 并注释回滚用法。批次/文档解析相关测试同步（默认断言、YAML 加载、回滚 env）。
- **验证**：`tests/unit/test_document_parser_factory.py` 10 passed；宽测 1163 passed（16 个既有 env/proxy/prompt
  失败与基线一致，非本次引入）；`git diff --check` 干净。

### P7.2 文档/回滚改写
- README 新增「文档解析（DocReader 迁移）」节：`docreader` 默认解析能力、`DOCUMENT_PARSER_BACKEND=legacy`
  回滚命令、旧 Loader 图片分类/metadata 合同迁移要点。
- 灰度演练（a007c73，in-process 真实 gRPC md/docx/pdf SUCCESS）+ 主侧 docreader 链可达已记录。

### P7.1b feat(parser): wire docreader backend into CLI and web pipeline construction
- 早期 Phase 1 只在独立 factory 支持后端路由；运行时 CLI/Web 摄取入口实际始终走旧 `LoaderRegistry`，
  未真正消费 `document_parser.backend`。本提交补齐这处缝隙：
  - `src/document_parser/grpc_transport.py`：真实 gRPC transport（经 docreader 生成 stub，`_load_proto`
    兼容 services.docreader 与 docreader 两种布局，lazy import 不污染 stub-free 单元测试）。
  - `src/document_parser/factory.py::build_document_parser_from_settings`：按 backend 构建
    docreader（注入或新建 grpc transport → `DocReaderClientParser`）/ legacy（loader 适配）。
  - `scripts/ingest.py main()` 与 `src/application/engines.py::_build_pipeline`：据此切换，将
    `DocumentParserLoader` 桥接入 pipeline loader 槽；构造失败（如服务包/proto 不可达）则回退
    `LoaderRegistry`，保持分支始终可运行。
- **验证**：`tests/unit/test_document_parser_grpc_transport.py`（in-process 真实 gRPC：读流+引擎列表+断连
  ConnectionError）、factory 新用例；affected 84 passed（含 e2e/test_web_api_endpoints real-ingest 全过）；
  `git diff --check` 干净。

### 未完成（需部署后推进）
- P7.3 删除重复的 PDF/Markdown Loader：**前置=至少一个发布周期无回滚 + 完成一次真实业务文档灰度**。删除前
  须把旧 Loader 的 `ImageRef(is_content, classification_reason)` 合同完整并入新链路（视觉变换已按
  `is_content` 过滤装饰图，缺省 True）。
- P7.4 更新 OpenAPI / 部署文档 / 运维检查表（随删除一起做）。

## Phase 7 审核交接（P7.review）—— 迁移实施完成
> WeKnora DocReader 受控迁移（Phase 0→7）实施完毕。缺省已切 `docreader` 且 CLI/Web 摄取入口真正消费该
> feature-flag（P7.1b 补齐运行时路由缝隙），回滚开关 `DOCUMENT_PARSER_BACKEND=legacy`；删除旧 Loader 与最终
> OpenAPI/检查表留待部署灰体验证后（计划门控）。

### 1) 各 Phase 交付
| Phase | 交付 |
| --- | --- |
| 0 | 固定样本集+基线+来源/许可+独立服务骨架 |
| 1 | 统一契约/客户端/feature-flag/桥接 + DocReader gRPC 服务端+部署探活 |
| 2 | PDFParser 迁移（pymupdf 后端改写）：逐页分类/多栏/去噪/扫描渲染/嵌入式图 |
| 3+4 | 本地 OpenDataLoader 引擎 + GFM/HTML 表格规范化 + 表格感知分块（同时交付） |
| 5 | 本地 Qwen3.8-27B 多模态 OCR/Caption 子分块（能力门+真实图探测+索引路由） |
| 6 | 七类格式（依赖自由）：DOCX/CSV/XLSX/PPTX/legacy门控/HTML/MHTML/EPUB/XMind/图片 |
| 7 | 默认切 docreader + env 回滚 + 文档；灰度演练（真实 gRPC md/docx/pdf SUCCESS） |

### 2) 测试与结果
- services/docreader 全量 88 passed（含多格式灰度演练）；主工程本迁移相关 65+ passed；上传相关 25 passed。
- 既有环境失败 16 项（socks proxy / prompt 跨挂载）与基线一致，非本迁移引入。
- 全量 tests/unit 1166 passed + 16 既有失败 + 1 skip（grpc transport 用例在无 PYTHONPATH 语境下按预期跳过）；
  `services/docreader/tests` 88 passed（含该 transport 用例运行时 1.12s 通过）。
- 工作区干净；`git diff --check` 通过。

### 3) 部署期待办（超出本离线环境）
1. 真实业务文档灰度一次（docx/pdf/xlsx 等）并确认无回滚。
2. P7.3 删除重复 PDF/Markdown Loader——删前并入 `is_content/classification_reason` 合同。
3. P7.4 更新 OpenAPI、README、部署文档与运维检查表。
4. 在线本机 VLM 端到端灰度（supports_vision 真实图探测 + 扫描页 OCR）。

---

## Phase 5 审核交接（T5.4）
> 相位 5 交付：本机 Qwen3.8 27B（OpenAI 兼容）多模态 OCR/Caption 子分块，触发条件、失败语义、索引路由。

### 1) 提交列表
| 提交 | 内容 |
| --- | --- |
| `b057679` | feat(vision): add multimodal ocr/caption sub-chunk producer with capability gate |
| `7e86766` | feat(vision): wire vision-ingest transform with triggers and failure semantics; recognize qwen3.8-27b |
| `9b03388` | feat(vision): route ocr/caption sub-chunks through embedding, bm25 and vector index |

### 2) 完成内容
- 子分块生产器（T5.1）：capability 视觉门 + 真实 1×1 PNG 探测（supports_vision 必须过一次真实图校验）、
  OCR/Caption 提示、拒答/提示回显/空/无效清理。
- 摄入接线（T5.2):触发条件（force_vision / scanned_pdf / 内容图 / 图片表格 / 文本过短 <30 字）、
  失败语义（单图失败告警继续；扫描全失败抛错绝不标成功；数字附属图失败→partial_success）、feature-flag
  默认 `enabled=False`（不改变现有摄入路径）；`VISION_MODELS` 增补 `Qwen3.8-27B`。
- 索引路由（T5.3）：子分块展开为可索引 Chunk，进入 Embedding/BM25/向量。
- 复用既有 `src/libs/llm`（capability_validator/ContentBlock/VISION_MODELS）；未引入任何外部付费 VLM。

### 3) 测试与结果
- 视觉相关 14 passed（production+transform）；相关摄入/分块 108 passed；batch_processor+ingestion_service
  29 passed；services/docreader 59 passed；`git diff --check` 干净。

### 4) 已知限制
- 本环境离线：无本机 Qwen3.8 27B 在线端点可打，真实图探测与端到端灰度以 mock 覆盖；部署端需一次在线
  `supports_vision_probe` + 灰度（Phase 3 交付清单同门）。
- 子分块展开后混入 chunks 列表走同一 batch embedding；若需独立 embedding 模型/路由，属 Phase 后续可选增强。

### 5) 下一阶段建议
- 进入 **Phase 6**：格式扩展（按序 DOCX → XLSX/CSV → PPTX → DOC/XLS/PPT → TXT/HTML/MHTML → EPUB/XMind →
  图片），每格式同时交付解析器/依赖/路由/上传白名单/Magic+MIME 校验/测试样本/验收测试，禁止一次 PR 混入全部格式。
  首个任务：DOCX 解析器（本地、无专有依赖则走 opendataloader 型后端）。

---

## Phase 3+4 审核交接（T3.5/T4.2）
> Phase 3 与 Phase 4 按计划「同时交付」共同验收。

### 1) 提交列表
| 提交 | 内容 |
| --- | --- |
| `f1eb696` | feat(docreader): add table/markdown normalization (gfm, clean html, path hygiene) |
| `8d46f7e` | feat(docreader): add local opendataloader engine, registry routing and scanned fallback |
| `bb6c6d8` | feat(chunking): add table-aware chunking with protected spans and context headers |

### 2) 完成内容
- 本地 OpenDataLoader 引擎 `opendataloader`（裁剪：本地 only、可用性门控 Java+包、扫描回退）；registry
  改为 (fmt,name) 复合键支持多引擎；`parse_file(parser_engine=...)` 显式选引擎；list_engines 上报可用性。
- GFM/HTML 表格规范化与路径/base64 卫生（table_normalize）。
- 表格感知分块（table_protection + DocumentChunker 表感知路径）。

### 3) 测试与结果
- services/docreader 全量 59 passed；主项目 chunker/splitter/parser/config 相关 152 passed（不含既有
  prompt 相关 4 个环境失败：MODULAR-RAG-MCP-SERVER 挂载，非本迁移引入）。`git diff --check` 通过，工作区干净。

### 4) 已知限制
- OpenDataLoader 真实 convert 需 `opendataloader-pdf` 包 + Java；本环境离线无包，真实 convert 未跑（引擎以
  available=False+原因上报，测试以 mock 覆盖 convert/回退/规范化）。
- 表格感知分块在本地 PDFParser 输出的 GFM 表上验证；HTML rowspan 表按行拆分近似（注入的 `<table>` 标签使
  offset 回退为单调，不破坏索引）。
- 扫描页/图 OCR 仍由主服务侧 Phase 5 负责。

### 5) 下一阶段建议
- 进入 **Phase 5**：本地 Qwen3.8 27B 多模态入库（扫描页/图片表格 OCR、表格重建、Caption），触发条件
  `scanined_pdf`/有效文档图。首个小任务：接入本地多模态引擎的文档图分批请求与失败回退。

---

## Phase 1 进度（进行中）

### T1.0 feat(parser): add parsed document contract
- **内容**：新增 `src/document_parser/{__init__.py,types.py,errors.py}`，含
  `ParseRequest`（frozen）+ `ParsedImage` + `ParsedDocument` + `ParseStatus`
  （success/partial_success/failed），以及稳定错误分类 `ParseError` /
  `UnsupportedFormatError` / `EngineUnavailableError` / `ParseTimeoutError` /
  `ParseFailedError`(带 attempts) / `ImagePersistenceError`。
- **设计决策**：契约纯 stdlib、无依赖，客户端/适配器/测试均可廉价导入；`ParseRequest` 仅
  接受 bytes（内部统一为 bytes）；`engine_overrides` 复制语义（frozen、不共享调用方 dict）。
  `ParsedDocument` 不决定存储路径（主服务持久化并重写引用），含 partial_success 语义位。
- **已修改文件**：`src/document_parser/__init__.py`、`types.py`、`errors.py`、
  `tests/unit/test_document_parser_types.py`
- **测试**：`pytest tests/unit/test_document_parser_types.py` → 11 passed。
- **未完成**：T1.1 起（protocol / adapter / client / flag / pipeline 接线）。

### T1.1 feat(parser): add parsed parser protocol
- **内容**：`src/document_parser/base.py` 新增 `ParserEngineInfo`（name/formats/available/unavailable_reason/extra）
  与 `DocumentParser`（`runtime_checkable` Protocol：`parse` + `list_engines`）。
- **测试**：`test_document_parser_base.py` → 5 passed。

### T1.2 feat(parser): add legacy and parsed-document adapters
- **内容**：`src/document_parser/adapters.py` 新增 `sanitize_file_name`/`guess_mime`、
  `LegacyLoaderParserAdapter`（把现有 `BaseLoader` 包装成 `DocumentParser`：字节写临时目录→loader→
  读回图片字节为内存 `ParsedImage`）、`ParsedDocumentAdapter`（新旧输出统一 `ParsedDocument -> Document`
  的单一收敛点，id 确定性哈希）。
- **设计决策**：图片字节回读为内存 `ParsedImage`，让统一 image 持久化路径有单一数据源；文件名
  basename 净化（§11）。
- **测试**：`test_document_parser_adapters.py` → 7 passed。

### T1.3 feat(parser): add docreader streaming client
- **内容**：`src/document_parser/client.py` 新增传输抽象 `DocReaderTransport` +
  流帧 `ReadStreamMeta`/`StreamFrame` + `DocReaderClient`。读取流先 meta 后逐个 image；
  meta.error、image 先于 meta、空流均转 `ParseFailedError`。
- **设计决策**：客户端依赖注入 transport，不直接 import 生成的 pb2 → 可假 transport 单测、无需 gRPC 桩即可
  开发；真实 pb2 transport 在接线时提供。错误语义按 §5 故障处理不给吞错。
- **测试**：`test_document_parser_client.py` → 7 passed。

### T1.4 feat(parser): add docreader parser and backend feature flag
- **内容**：新增 `DocumentParserSettings`（backend/enabled/endpoint/request_timeout_seconds/max_file_bytes/
  default_engine，带字段校验：backend ∈ {legacy,docreader}、timeout>0）并入 `Settings`；`config/settings.yaml`
  新增 `document_parser:` 块（默认 legacy）。`docreader_parser.py` 新增 `DocReaderClientParser`；
  `factory.py` 新增 `build_document_parser(...)`（backend=legacy→LegacyLoaderParserAdapter；docreader→需
  transport，否则抛 `EngineUnavailableError`；enabled=false→回退 legacy）。
- **测试**：`test_document_parser_factory.py` → 9 passed；config 相关测试全绿。
- **关键**：`config/settings.yaml` 模型名仍为 `Qwen3.6-35B-A3B-NVFP4`（见已知问题，Phase 5 统一）。

### T1.5 feat(ingestion): bridge unified parser into pipeline behind flag
- **内容**：新增 `src/document_parser/loader_adapter.py` 的 `DocumentParserLoader(BaseLoader)`，把统一
  `DocumentParser` 桥接回 pipeline 的既存 `BaseLoader` 槽位（load: 读文件→ParseRequest→parser→
  `ParsedDocumentAdapter`）。`scripts/ingest.py build_pipeline` 增加可选 `document_parser` 参数：提供时用
  `DocumentParserLoader`，否则沿用 `LoaderRegistry`（默认 legacy 不变）。
- **设计决策**：不清扫/重写 pipeline，保持 `BaseLoader` 接口与既有测试不破坏；新链路默认被 flag 隔离。
- **测试**：`test_document_parser_loader_adapter.py` → 3 passed；`test_ingestion_pipeline.py` → 26 passed
  （既有未回退）。

### T1.6a feat(docreader): port parser concurrency limiter from weknora
- **内容**：`services/docreader/docreader/parser/concurrency.py` —— **直接迁移**自上游
  `docreader/parser/concurrency.py`（`parser_worker_limit`，进程级 `BoundedSemaphore`），仅改包路径。
- **测试**：`services/docreader/tests/test_parser_concurrency.py`（来自上游回归思想）→ 通过。

### T1.6b feat(docreader): port base parser and result model
- **内容**：`services/docreader/docreader/models/document.py` —— **参考重写**自上游
  `docreader/models/document.py`，保留 `content/images/metadata` 契约，**不迁移**上游遗留 Chunk（§3.2）；
  `to_json` 以字节长度代替 base64，避免泄漏。`services/docreader/docreader/parser/base_parser.py` ——
  **基本迁移**自上游 `base_parser.py`（bytes→Document 契约、parse_into_text/parse）。
- **测试**：`services/docreader/tests/test_base_parser.py` → 8 passed（含上游不转小写扩展名的忠实行为）。

### T1.6c feat(docreader): port parser chain (not swallow final error)
- **内容**：`services/docreader/docreader/parser/chain_parser.py` —— **基本迁移**自上游 `chain_parser.py`
  （FirstParser/PipelineParser/create 工厂）。**强制适配**（§6）：`FirstParser` 全部失败时改为抛
  `ChainParseError(attempts=...)`，不静默返回空 Document（不吞最终错误、返回完整尝试记录）。
  仅内联 `encode_bytes`（上游 endecode 依赖 numpy/PIL 未使用则不迁，§6.1）。
- **测试**：`test_chain_parser.py` → 通过。

### T1.6d-1 feat(docreader): add registry + parser facade (trimmed)
- **内容**：`parser/registry.py` —— **裁剪迁移**自上游 `registry.py`（去掉云引擎；引擎懒加载避免
  import 即载重型依赖；`list_engines`/`parse_file`/`engine_for`）。`parser/parser.py` —— Facade，
  含 `parse_file` 与 `parse_url`（URL 解析本轮不开放，抛 `UnsupportedError`）。
  `parser/text_parser.py` —— 本地 txt/md 直通占位，使服务 Phase 1 即可运行/探活。
- **测试**：`test_registry.py`、`test_server.py` → 通过。

### T1.6e feat(docreader): add generated grpc stubs + entrypoint
- **内容**：`proto/docreader.proto`（契约迁移）+ 上游生成式 `docreader_pb2*.py`（grpcio-tools 离线不可装，
  使用上游已生成 Python stub，实为契约迁移产物，去掉 Go 依赖）。
  `main.py` —— **裁剪迁移**自上游 `main.py`：去掉 auth/TLS、MinIO/云持久化、URL 分支、request-id
  线程上下文；保留 unary Read + 流式 ReadStream（先 meta 后逐 image）+ ListEngines + 可选健康检查
  （grpc_health 仅在可导入时启用）。`config.py` —— 本地配置，启动期失败即报（§9）。
  服务现可真实通过 gRPC 运行与探活。
- **测试**：`test_proto_contract.py`、`test_server.py`、`test_server_e2e.py`（in-process gRPC 走线）
  → services/docreader 全量 28 passed。

### T1.6f chore(docreader): add optional compose service and dev probe
- **内容**：`docker-compose.yml` 新增 **profile-gated** `docreader` 服务（默认不启动；
  `docker compose --profile docreader up -d`）；`Makefile` 新增 `docreader` /
  `docreader-probe` / `docreader-probe-remote` 目标；`scripts/docreader_probe.py` 探活脚本。
- **验证**：后台启动真实服务后 `python scripts/docreader_probe.py 127.0.0.1:50123` →
  `OK engines=['builtin']`（exit 0），再 job_kill 停服；in-process gRPC e2e 通过。
- **安全/清洁**：默认 legacy 不受影响；未提交调试数据。

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
      —— 已写入下方「Phase 0 审核交接」，待审核通过后进入 Phase 1。

---

## Phase 1 未完成小任务（按应做顺序）

- [x] T1.0 解析契约（types/errors + tests）
- [x] T1.1 `DocumentParser` protocol + `ParserEngineInfo` + tests
- [x] T1.2 `LegacyLoaderParserAdapter` + `ParsedDocumentAdapter` + tests
- [x] T1.3 DocReader 客户端 `client.py`（流式/超时/错误语义）+ tests
- [x] T1.4 `DocReaderClientParser` + Feature Flag（`document_parser.backend: legacy|docreader`）+ tests
- [x] T1.5 pipeline load 阶段改为接受统一 `DocumentParser`（`DocumentParserLoader` 桥接，默认 legacy）
- [x] T1.6a `services/docreader` 迁移 `concurrency.py`（直接迁移）
- [x] T1.6b `services/docreader` 迁移 `base_parser.py` + `models/document.py`（基本/参考重写）
- [x] T1.6c `services/docreader` 迁移 `chain_parser.py`（FirstParser/PipelineParser，不吞最终错误）
- [x] T1.6d `services/docreader` 迁移 `registry.py`（裁剪无云引擎）+ `parser.py` Facade + text_parser
- [x] T1.6e 生成式 gRPC stub（docreader.proto）+ `main.py` entrypoint + 可选健康检查
- [x] T1.6f docker-compose DocReader 服务（profile 隔离）+ `Makefile`/`start_dev.sh` 探活
- [x] T1.7 汇总 Phase 1 审核材料（见下文「Phase 1 审核交接」）

---

## Phase 1 审核交接（T1.7）

> 本节点标志 Phase 1 的全部小任务完成；审核通过后再推进 Phase 2。

### 1) Phase 1 提交列表（自 Phase 0 之后）
| 提交 | 内容 |
| --- | --- |
| `307a81c` | feat(parser): add parsed document contract |
| `7063a2a` | docs: record phase 1 contract task progress |
| `a6948da` | feat(parser): add parsed parser protocol |
| `fd5f91c` | feat(parser): add legacy and parsed-document adapters |
| `1ad27b7` | feat(parser): add docreader streaming client |
| `b6e138e` | feat(parser): add docreader parser and backend feature flag |
| `a36e7c0` | feat(ingestion): bridge unified parser into pipeline behind flag |
| `99d3f79` | docs: record phase 1 contract through pipeline bridge |
| `6d87806` | feat(docreader): port parser concurrency limiter from weknora |
| `b129fa3` | feat(docreader): port base parser and result model from weknora |
| `19c6ef2` | docs: record docreader service-core ports |
| `4ecb154` | feat(docreader): port parser chain and don't swallow final error |
| `31c6231` | feat(docreader): add generated grpc stubs and proto contract |
| `5222246` | feat(docreader): add grpc service entrypoint, registry and facade |
| `d6f222e` | chore(docreader): add optional compose service and dev probe |

### 2) 完成的小任务清单
- T1.0 解析契约；T1.1 Protocol；T1.2 适配器；T1.3 流式客户端；T1.4 Feature Flag + ClientParser；
  T1.5 pipeline 桥接；T1.6a–f DocReader 服务端核心 + gRPC + 部署/探活。

### 3) 主要文件变更
- 新增 `src/document_parser/`（types/errors/base/client/adapters/factory/docreader_parser/loader_adapter）。
- 新增 `services/docreader/` 运行级内容（models/parser/* /proto/* /config/main + tests）。
- `src/core/settings.py` + `config/settings.yaml` 新增 `document_parser` 配置（默认 legacy）。
- `scripts/ingest.py build_pipeline` 可选 `document_parser` 参数。
- `docker-compose.yml`（docreader profile）、`Makefile`（docreader 目标）、`scripts/docreader_probe.py`。

### 4) WeKnora 代码迁移映射（Phase 1）
| 上游 | 迁移方式 | 备注 |
| --- | --- | --- |
| `docreader/parser/concurrency.py` | 直接迁移 | 仅改包路径 |
| `docreader/models/document.py` | 参考重写 | 去掉遗留 Chunk（§3.2） |
| `docreader/parser/base_parser.py` | 基本迁移 | 包路径/导入 |
| `docreader/parser/chain_parser.py` | 基本迁移+适配 | 全部失败不吞错误，raise ChainParseError(attempts) |
| `docreader/parser/registry.py` | 裁剪迁移 | 去云引擎、懒加载 |
| `docreader/parser/parser.py` | 裁剪迁移 | parse_url 本轮不开放 |
| `docreader/proto/*` | 契约迁移 | 用生成 Python stub，去 Go |
| `docreader/main.py` | 裁剪迁移 | 去 auth/TLS/MinIO/URL/request-id ctx |

### 5) 测试命令和结果
- `pytest tests/unit/test_document_parser_*.py tests/unit/test_config*.py test_config_loading.py
  test_loader_*.py test_document_chunker.py` → 169 passed。
- `PYTHONPATH=services/docreader pytest services/docreader/tests` → 28 passed（含 in-process gRPC e2e）。
- 真实服务后台启动 + `scripts/docreader_probe.py` → `OK engines=['builtin']`。
- `git diff --check` 通过（源文件）；二进制 PDF 夹具仅固有 xref 内容除外。
- 全量 `tests/unit` 仍为既有 16 个失败（环境 socks 代理 / prompt 相关，非本迁移引入）。

### 6) 基准数据前后对比
- 本阶段不改变解析结果质量（仍默认 legacy），只是把解析入口抽象为统一 `DocumentParser` 并按 flag 切换；
  迁移后的质量改进自 Phase 2（PDFParser）起由基线样本对比衡量。

### 7) 已知限制
- DocReader 服务当前仅有 txt/md 直通解析（`PlainTextParser`）；PDF/DOCX/XLSX/PPTX 等在 Phase 2/3/6 迁移。
- gRPC 标准健康协议依赖 `grpcio-health-checking`（services/docreader pyproject 已声明）；当前共享 venv 未装，
  服务端在不可导入时跳过健康服务、用 `ListEngines` 探活（已有脚本）。
- `grpcio-tools` 未装（venv 无 pip），proto 用上游已生成 Python stub + 本地 `.proto` 协同维护；
  接入 CI/生成流程时再补充 `docreader/scripts/generate_proto.sh` 对应的本地脚本。
- 服务端厂商未纳入本轮（read_config/厂商 reserved）。

### 8) 回滚方法
- 默认 backend=legacy，`document_parser.backend` 单配置切回，无需数据迁移。
- DocReader 服务是独立 profile 容器/独立 Makefile 目标，未启用时不运行、不影响主服务启动。
- 如需整体回退：`git revert` 本轮 15 个提交即可恢复 Phase 0 之后的基线（Phase 0 内容为纯新增，可留可撤）。

### 9) 下一阶段建议
- 进入 **Phase 2**：迁移 WeKnora 内置 PDFParser（逐页分类/多栏/标题/噪声清理/扫描页与图片渲染），
  配合基线样本验收（双栏不交错、扫描页全部生成页面图、混合 PDF 只渲染扫描页、malformed 稳定错误码）。
  首个建议小任务：`feat(docreader): port pdf page classification`（含上游 `test_pdf_router.py` 适配）。

---

## Phase 2 审核交接（T2.6）

> 本节点标志 Phase 2 完成。需要说明：本阶段把上游 pypdfium2 后端改写为 pymupdf（计划 §10），
> 保留算法、逐份提交、每个带测试，并在固定基线样本上做了回归对比。

### 1) Phase 2 提交列表
| 提交 | 内容 |
| --- | --- |
| `4750167` | docs: record phase 2 start and t2.1 |
| `d805461` | feat(docreader): port pdf page classification (pymupdf backend) |
| `8173350` | feat(docreader): port layout-aware multi-column pdf text over pymupdf |
| `a272c3a` | feat(docreader): port pdf text post-processing (noise cleanup) |
| `dc30309` | docs: record phase 2 t2.1-t2.3 |
| `07dc4b3` | feat(docreader): port routed pdf parser (text/scanned/hybrid) over pymupdf |
| `b495411` | test(docreader): wire real pdf through grpc readstream e2e |

### 2) 完成的小任务
- T2.1 逐页分类；T2.2 layout 多栏/阅读顺序；T2.3 文本后处理（净化/去噪）；T2.4 PDFParser 路由
  （文本/扫描/混合 + 扫描页渲染 + 嵌入式图提取）+ registry 注册 + 基线回归 + gRPC 走线。

### 3) 新增文件
- `services/docreader/docreader/parser/{pdf_classify,pdf_layout,pdf_postprocess,pdf_parser}.py`
- `services/docreader/tests/{test_pdf_classify,test_pdf_layout,test_pdf_postprocess,test_pdf_parser}.py`
- `config.py` 增加 pdf_render_* / pdf_jpeg_quality（启动期校验）。

### 4) 迁移映射（Phase 2）
| 上游 | 迁移方式 | 备注 |
| --- | --- | --- |
| `_classify_page` | 直接迁移 | pdf_classify 纯函数 |
| `_page_image_area_ratio` / `_extract_page_text` | 后端改写 | pymupdf image_info / get_text |
| layout 系列（XY-cut 等） | 逐字迁移 + `page_chars` 改写 | pymupdf rawdict，y 翻转为底左原点 |
| 文本后处理 | 逐字迁移 | pdf_postprocess |
| PDFParser 路由/扫描渲染/embedded | 算法迁移 + 后端改写 | pymupdf pixmap/extract_image；images 存原始字节 |
| `_strip_repeating_lines` | 直接迁移 | |
| `_extract_vector_figure_clips` | 未迁（默认关闭） | 留作后续 |

### 5) 测试命令和结果
- `PYTHONPATH=services/docreader pytest services/docreader/tests` → 48 passed。
- 核心契约/配置测试 → 157 passed；`git diff --check` 通过；工作区干净。
- 基线回归：two_column 按列线性化；scanned 渲染页面图；bordered/borderless/cross_page/single_column
  走文本层；malformed/空字节抛干净异常（服务端包成 error）。

### 6) 基准数据前后对比
- 语义对比（非数值）：PDFParser 输出质量变化见图表（双栏不交错、扫描页生成图、表内容保留）。
  数值基线对比待 Phase 7 切换默认后由 `run_parsing_baseline.py` 产出正式指标。

### 7) 已知限制
- 矢量图区渲染（chart region）默认关闭；隐藏文本（render-mode 3）过滤未含；扫描渲染进程内串行。
  三者均为后续小任务，不阻塞本阶段。
- 本项目 DocReader 不做 OCR/VLM：扫描页/图输出原始图像字节，由主服务侧（Phase 5 本地 Qwen3.8 27B）
  负责识图/入库。

### 8) 回滚方法
- 默认 backend=legacy 不受影响；PDFParser 仅在 `--backend docreader` 时被调用。
- 未迁移的上游不变量（video/表格跨页等）不影响 legacy 路径。

### 9) 下一阶段建议
- 进入 **Phase 3**：OpenDataLoader 与表格规范化（Plan §O）。按计划拆小任务、逐份提交、基线回归。

## Phase 0 审核交接（T0.5）

### 1) 本阶段提交列表
| 提交 | 内容 |
| --- | --- |
| `1ca9ad3` | docs: add docreader migration progress record |
| `3891171` | docs(baseline): add weknora provenance and corpus layout |
| `fca99a4` | test(baseline): add fixed parsing samples and legacy baseline metrics |
| `4f27df4` | chore(docreader): add standalone service skeleton |

### 2) 完成的小任务清单
- T0.0 调研：确认分支/上游提交/差异证据，记录已知基线问题。
- T0.1 固定样本集：7 个非敏感样本（单栏/双栏/有框/无框/跨页表格 PDF、扫描 PDF、Markdown）。
- T0.2 基线运行器 + 基线指标快照 `metrics/baseline-2026-09-09.json`。
- T0.3 第三方来源清单 `provenance.md`（含许可：WeKnora MIT；utils/__init__.py InfiniFlow Apache-2.0）。
- T0.4 DocReader 独立服务骨架（core gRPC 依赖 + 容器 + 版权通告 + 空包占位 + 骨架测试）。

### 3) 主要文件变更
- 新增 `scripts/generate_parsing_baseline_samples.py`、`scripts/run_parsing_baseline.py`。
- 新增 `docs/baselines/document-parsing/{README.md,provenance.md,descriptors/future-formats.md,samples/,metrics/}`。
- 新增 `services/docreader/**`（package/parser/model/proto/utils 占位 + config/main 占位 + 测试）。
- 新增 `docs/implementation-status/document-parser-migration.md`。

### 4) WeKnora 代码迁移映射
- 本阶段**未迁移任何上游代码**（Phase 0 仅骨架与来源登记）。完整逐文件映射见 `provenance.md`。

### 5) 测试命令和结果
- `pytest tests/unit/test_loader_pdf_contract.py test_loader_markdown_contract.py
  test_loader_factory.py test_document_chunker.py test_core_types.py` → 115 passed。
- `PYTHONPATH=services/docreader pytest services/docreader/tests` → 3 passed。
- `python scripts/generate_parsing_baseline_samples.py` 与 `python scripts/run_parsing_baseline.py` 均成功。
- `git diff --check`：仅二进制 PDF 内部的 xref 表固有尾随空格被标注，非源代码问题；源文件无白错误。
- 全量 `tests/unit` 仍为既有 16 个失败（环境 socks 代理 / prompt 相关，见已知问题），未新增。

### 6) 基准数据前后对比
- 基线（legacy loader 现状）：表格期望 ASCII 单元格字符串 100%文本存活，但**无表格结构**（列/表头保持
  实为 0/不可测）；`two_column.pdf` 提取顺序交错（具体次序见 metrics JSON 与 README）；`scanned.pdf`
  无真实文本、1 图片占位、无 OCR/VLM 路径；短样本各 1 chunk。
- 迁移目标：Phase 2/3 恢复多栏顺序 + 表格结构，Phase 4 表格保护/表头跨块，Phase 5 扫描页/图片表格
  VLM。目标门槛见 plan §13（表格关键数字≥95%、双栏 100% 不交错、扫描页 100% 覆盖等）。

### 7) 已知限制
- 无 docx/xlsx/pptx 二进制样本（Phase 0 依赖约束 + 这些格式尚未迁移），仅期望结构描述符。
- PDF 样本文本为 ASCII（pymupdf base-14 无 CJK）；中文向量在真实文档迁移后验证。
- `services/docreader` 尚无真实 entrypoint/解析器（Phase 1 起迁移）。

### 8) 回滚方法
- Phase 0 仅新增目录/脚本/文档，未触碰任何稳定链路。删除时 `git revert` 四个提交即可；
  `services/docreader` 与 `docs/baselines` 均为新增，不影响现有启动方式。

### 9) 下一阶段建议
- 进入 **Phase 1**：迁移 BaseParser/Parser/Registry/ChainParser/Concurrency + 生成式 gRPC stub
  + `src/document_parser/` 契约（`ParseRequest`/`ParsedDocument`/`ParsedImage`）+ 客户端 + 适配器 +
  Feature Flag。建议首个小任务：`feat(parser): add parsed document contract`（纯契约 + 测试，无运行集成）。

---

## 已知问题清单

1. `web_api/settings.py` 上传白名单 MIME/扩展名不一致（含 `text/plain` 无 `.txt`）。计划 Phase 6 修复。
2. `config/settings.yaml` 视觉模型名 `Qwen3.6-35B-A3B-NVFP4` 与实际本地 Qwen3.8 27B 不一致。计划 Phase 5 统一。
3. 全量 `tests/unit` 16 个既有失败（见 T0.0），与本迁移无关，作为基线记录。
4. 当前 PDF Loader 无表格检测、无双栏阅读顺序恢复。这是本迁移的核心改进点（Phase 2/3/4）。

---

## 工作区状态

- 未提交改动：见 `git status`（每完成一批即提交清空）。
- 提交策略：遵循规范——小提交、每提交解决一个问题、`git diff --check` 校验（二进制 PDF 夹具除外）、不在无谓文件上重写。