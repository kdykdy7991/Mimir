# 本地文档解析能力增强：WeKnora DocReader 受控迁移与集成方案

> 状态：待实施  
> 文档日期：2026-09-09  
> 目标分支：`feature/weknora-inspired-optimizations`  
> 当前项目基线：`b8b0668` / `pre-weknora-optimization-20260909`  
> 参考项目：`/home/hello/workspace/WeKnora`  
> 参考提交：`3e6010e7cd3937f289cc1dbadc829e71eb1163f4`  
> 实施性质：已有代码的受控迁移、裁剪、适配与集成，不是从零重写

## 1. 决策摘要

本轮将当前以 PDF、Markdown 基础文本抽取为主的 Loader，升级为本地运行的多格式、结构感知、多模态增强解析体系。

核心决策：

1. 迁移 WeKnora `docreader` 中可独立运行的 Python 解析能力及其测试。
2. 解析重依赖放入独立 DocReader 服务，避免 LibreOffice、Java、Playwright、PDF 渲染和大文件处理拖垮 Web API/MCP 查询进程。
3. 当前项目继续负责分块、图片持久化、Embedding、BM25、向量入库和检索。
4. 普通数字 PDF 优先使用确定性的本地文本/版面解析；OpenDataLoader 作为表格和复杂版面增强引擎。
5. 扫描页、图片表格和有效文档图片由本地 Qwen3.8 27B 执行 OCR、表格重建和 Caption。
6. 保留模型能力声明、多模型路由和纯文本降级结构，但本期只实现、部署和验收本地 Provider。
7. 不接入任何外部付费 API。
8. 不迁移 MinerU、PaddleOCR-VL、anydoc、WeKnora Cloud 及 WeKnora 的 Go 业务控制层。
9. 表格解析与表格感知分块必须同时交付；只增强抽取、不保护分块不算完成。

## 2. 当前实现与目标差距

### 2.1 当前实现证据

- `src/libs/loader/loader_factory.py:29-33` 只注册 PDF、Markdown 两类 Loader。
- `src/web_api/settings.py:55-64` 上传白名单只覆盖 PDF、Markdown；MIME 包含纯文本但扩展名没有 `.txt`，存在配置不一致。
- `src/libs/loader/pdf_loader.py:139-152` 将 PDF 文本处理为带坐标的普通文本行。
- `src/libs/loader/pdf_loader.py:213-218` 按 `(y, x)` 排序文本和图片，不能正确表达双栏阅读顺序和表格行列关系。
- 当前 PDF Loader 未使用 `find_tables()` 或其他表格结构识别能力。
- `src/ingestion/pipeline.py:235-304` 将加载和分块作为两个独立阶段，但 Loader 结果只有文本与图片元数据，没有表格或页面结构契约。
- `src/core/types.py:48-84` 的 `Document` 仅包含 `id/text/metadata`，图片被塞入通用 metadata。
- `config/settings.yaml:111-119` 使用普通递归字符切分，没有表格保护和跨块表头补充。
- 当前配置文件登记的模型为 `Qwen3.6-35B-A3B-NVFP4`，与实际运行的本地 Qwen3.8 27B 不一致。

### 2.2 目标能力

完成后系统应支持：

- 数字 PDF、扫描 PDF、混合 PDF 的逐页路由；
- 双栏/多栏阅读顺序、标题层级、页眉页脚和异常文本层处理；
- PDF 表格识别、行列恢复、Markdown/HTML 规范化；
- 扫描页和图片表格的本地多模态 OCR；
- 图片 OCR 和 Caption 独立入库并与父 Chunk 关联；
- 表格完整性保护和跨 Chunk 表头补充；
- DOC/DOCX、XLS/XLSX/CSV、PPT/PPTX、TXT、HTML/MHTML、EPUB、XMind 和常用图片格式；
- 主解析器、回退解析器、可用性检查、超时和并发限制；
- 解析过程、回退路径、图片处理和结果质量的可观测性。

## 3. 范围边界

### 3.1 本期纳入

- WeKnora Python DocReader 的基础契约、Facade、注册表、解析器链和并发限制。
- 内置本地解析器：PDF、DOC/DOCX、Excel、PPT/PPTX、Markdown、TXT、HTML、MHTML、EPUB、XMind、图片。
- 本地 OpenDataLoader PDF 解析引擎。
- DocReader 独立服务、健康检查和图片流式返回。
- 当前项目的 DocReader Client 和解析结果适配器。
- 本地 Qwen3.8 27B 的图片 OCR、扫描页结构提取和 Caption。
- HTML/Markdown 表格规范化。
- 表格感知分块及跨块表头追踪。
- 解析基准数据、回归测试和阶段指标。

### 3.2 本期明确排除

- WeKnora Go 主服务与 Go DocReader Client；
- anydoc Go/CGo 引擎；
- WeKnora Cloud；
- MinerU 本地版和云端版；
- PaddleOCR-VL 本地版和云端版；
- 其他外部付费文档解析、OCR 或视觉 API；
- WeKnora 多租户、Redis/Asynq、云对象存储实现；
- DocReader 中已经不再负责生产分块的旧 splitter；
- Agent、Wiki、知识图谱和自动问题生成。

### 3.3 保留但本期不扩展的架构点

- `supports_vision` 模型能力字段；
- 视觉/纯文本模型双路径；
- 多模型选择与能力探测；
- 通用 Provider、Base URL、API Key 配置结构；
- 图片直传失败时使用已生成 OCR/Caption 的降级路径。

这些扩展点不得引入任何实际云端付费依赖。本期生产默认固定为本地 Qwen3.8 27B。

## 4. 目标架构

```text
Web API / CLI / MCP ingestion
             │
             ▼
       IngestionPipeline
             │ ParseRequest
             ▼
    DocumentParser interface
       ├── LegacyLoaderAdapter（迁移期兼容）
       └── DocReaderClientParser
                    │ gRPC ReadStream
                    ▼
          独立 Python DocReader
       ┌────────────┼───────────────────┐
       │            │                   │
   builtin      markitdown       opendataloader
       │            │                   │
       └──────── Markdown + images + metadata
                    │
                    ▼
       图片持久化与引用重写（当前主服务）
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
    结构/表格分块       本地 Qwen3.8 27B
                           OCR + Caption
          │                    │
          └─────────┬──────────┘
                    ▼
        Embedding + BM25 + Vector Store
```

职责边界：

- DocReader：只负责文件/URL字节到 Markdown、图片字节和解析元数据。
- 主服务：负责文件身份、权限、图片持久化、分块、视觉增强、索引、状态和追踪。
- 本地 VLM：只处理解析器标记的扫描页、有效图片和质量不足页面，不默认重读全部数字 PDF。

## 5. 统一契约

### 5.1 新增解析请求

目标文件：`src/document_parser/types.py`

```python
@dataclass(frozen=True)
class ParseRequest:
    source_path: str
    file_name: str
    file_type: str
    content: bytes
    parser_engine: str | None = None
    request_id: str | None = None
    engine_overrides: dict[str, str] = field(default_factory=dict)
```

约束：

- `file_type` 必须由扩展名、MIME 和可信文件 Magic 联合确认；
- 原始上传文件名只用于显示和解析提示，不可直接作为临时路径；
- `engine_overrides` 只允许服务端白名单字段，不接受任意模块路径或命令参数。

### 5.2 新增解析结果

```python
@dataclass
class ParsedImage:
    filename: str
    original_ref: str
    mime_type: str
    data: bytes
    page: int | None = None
    is_original: bool = False

@dataclass
class ParsedDocument:
    markdown: str
    images: list[ParsedImage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
```

解析服务不得自行决定最终存储路径。主服务持久化图片后统一重写 Markdown 引用，并转换为现有 `Document`。

### 5.3 解析器接口

目标文件：`src/document_parser/base.py`

```python
class DocumentParser(Protocol):
    def parse(self, request: ParseRequest) -> ParsedDocument: ...
    def list_engines(self) -> list[ParserEngineInfo]: ...
```

迁移期提供 `LegacyLoaderParserAdapter`，避免一次性删除现有 Loader。新旧解析输出必须经过同一个 `ParsedDocument -> Document` 适配器。

## 6. 上游代码迁移矩阵

所有来源路径均相对于 `/home/hello/workspace/WeKnora`，固定参考提交为本文顶部提交。

| 上游模块 | 迁移方式 | 保留内容 | 必须适配/删除 |
| --- | --- | --- | --- |
| `docreader/models/document.py` | 参考重写 | Markdown、图片、metadata 结果模型 | 改为本项目 `ParsedDocument`；不迁移废弃 Chunk |
| `docreader/parser/base_parser.py` | 基本迁移 | bytes → Document 契约 | 包路径、异常和日志 |
| `docreader/parser/chain_parser.py` | 基本迁移 | FirstParser、PipelineParser | 禁止吞掉最终错误；返回完整尝试记录 |
| `docreader/parser/concurrency.py` | 直接迁移 | 进程内 BoundedSemaphore | 接入本项目设置和指标 |
| `docreader/parser/parser.py` | 基本迁移 | Facade、DOC/DOCX Magic 修正 | 请求类型、错误码、trace metadata |
| `docreader/parser/registry.py` | 裁剪迁移 | 引擎注册、格式路由、回退 | 删除云引擎；避免模块导入即加载所有可选依赖 |
| `docreader/parser/pdf_parser.py` | 重点迁移 | 页面路由、多栏、标题、噪声清理、扫描页和图片 | 设置读取、输出模型、日志；保留算法测试 |
| `docreader/parser/opendataloader_parser.py` | 裁剪迁移 | 本地 ODL、Markdown、图片、低文本回退 | 删除 MinerU参数和外部 Hybrid 地址；只允许本地地址 |
| `docreader/parser/docx_parser.py` | 重点迁移 | 段落/表格/图片及顺序 | 配置、进程池和输出模型 |
| `docreader/parser/docx_merge.py` | 直接迁移 | 垂直合并单元格填充 | 包路径与版权头 |
| `docreader/parser/doc_parser.py` | 裁剪迁移 | antiword/LibreOffice 本地转换 | 删除无用兼容路径；强制超时 |
| `docreader/parser/excel_parser.py` | 重点迁移 | 工作表、表格、合并单元格 | 输出统一 Markdown，限制行列和文件大小 |
| `xlsx_merge.py` / `xlsx_repair.py` | 直接迁移 | 合并填充与 ZIP 修复 | 安全限制、包路径 |
| `markitdown_parser.py` | 裁剪迁移 | Office/PPT/CSV 回退 | 并发限制、输出模型、禁用网络能力 |
| `ppt_convert.py` / `pptx_media.py` | 基本迁移 | LibreOffice 转换、媒体提取 | 子进程超时、临时目录、文件名净化 |
| `markdown_parser.py` | 基本迁移 | 图片引用和 Markdown 规范化 | 禁止未授权远程图片下载 |
| `html_parser.py` | 基本迁移 | HTML → Markdown | 大小限制、脚本/样式清理 |
| `mhtml_parser.py` | 基本迁移 | 主内容、内嵌图片、链接处理 | 外链策略与资源限制 |
| `epub_parser.py` | 基本迁移 | 章节顺序、图片、链接 | ZIP Bomb 防护 |
| `xmind_parser.py` | 基本迁移 | Sheet、层级、Notes | ZIP 条目数/大小/加密检查 |
| `image_parser.py` | 基本迁移 | 原图作为解析资产 | 交给主服务 VLM，不生成伪文本 |
| `web_parser.py` | 延后 | 本地网页解析思路 | 本期不开放 URL 解析；避免 Playwright/SSRF 扩大范围 |
| `docreader/proto/*` | 契约迁移 | ReadStream、ListEngines | 重生成 Python代码；删除 Go package 依赖 |
| `docreader/main.py` | 裁剪迁移 | gRPC、健康检查、流式图片、request_id | 认证、配置、错误结构和关闭流程适配 |

### 6.1 禁止事项

- 不得把上游文件内容散落复制进现有 `PdfLoader` 而不记录来源；
- 不得修改 `upstream/weknora` 中的算法文件来夹带本项目业务逻辑；
- 不得迁移未被目标能力使用的 utils、依赖和测试；
- 不得把远程 Endpoint、API Key 或云 Provider 作为默认配置；
- 不得将解析输出中的 base64 图片直接写入日志、Trace 或数据库 metadata。

## 7. 目标目录

```text
services/docreader/
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── README.md
├── THIRD_PARTY_NOTICES.md
├── docreader/
│   ├── main.py
│   ├── config.py
│   ├── models/
│   ├── parser/
│   ├── proto/
│   └── utils/
└── tests/

src/document_parser/
├── __init__.py
├── base.py
├── types.py
├── client.py
├── adapters.py
├── image_persistence.py
├── table_normalizer.py
└── errors.py

src/ingestion/chunking/
├── document_chunker.py
├── protected_blocks.py
└── table_header_tracker.py
```

`services/docreader/docreader/parser/` 尽量保持上游结构，项目差异集中在 `src/document_parser/` 适配层。

## 8. 分阶段实施

### Phase 0：基准、来源和骨架

交付：

1. 建立 `docs/baselines/document-parsing/`，保存代表性输入、期望结构和基线指标。
2. 样本至少包含：
   - 单栏数字 PDF；
   - 双栏数字 PDF；
   - 有边框表格；
   - 无边框表格；
   - 跨页表格；
   - 扫描 PDF；
   - 图片表格；
   - DOCX 合并单元格；
   - XLSX 多 Sheet；
   - PPTX 图片和表格。
3. 记录当前解析文本、Chunk、耗时、表格数字正确率和失败类型。
4. 创建第三方来源清单，逐文件记录来源路径、提交和许可证。
5. 创建 DocReader 独立依赖与容器骨架。

验收：当前结果可重复，样本不包含敏感生产资料，来源清单能够覆盖全部迁移文件。

### Phase 1：DocReader 核心与兼容接入

迁移 BaseParser、Parser、Registry、ChainParser、Concurrency、gRPC ReadStream 和 ListEngines。

主项目新增：

- `ParseRequest` / `ParsedDocument` / `ParsedImage`；
- `DocReaderClientParser`；
- `LegacyLoaderParserAdapter`；
- `ParsedDocumentAdapter`；
- 健康检查和超时；
- Feature Flag：`document_parser.backend: legacy | docreader`。

修改：

- `src/ingestion/pipeline.py`：load 阶段改为调用统一 DocumentParser；
- `src/application/composition.py`：组装客户端、兼容适配器与设置；
- `docker-compose.yml`：新增 DocReader 服务；
- `scripts/start_dev.sh`、`Makefile`：增加本地启动和探活；
- `config/settings.yaml`：新增解析配置。

验收：旧 PDF/Markdown 测试不回退；Feature Flag 可无数据迁移切回 legacy；DocReader 不可用时错误明确。

### Phase 2：WeKnora 内置 PDFParser

迁移并适配：

- 文本页/扫描页逐页分类；
- 多栏检测和阅读顺序；
- 标题恢复；
- 页眉页脚、边栏和异常字符清理；
- plain/layout 质量比较与回退；
- 嵌入图片、矢量图和扫描页渲染；
- PDFium 全局并发保护和扫描渲染进程限制。

不得在此阶段自行新增表格识别算法；表格由 Phase 3 处理。

验收：

- 双栏样本不交错；
- 扫描页全部生成页面图片；
- 混合 PDF 只渲染扫描页；
- 数字 PDF 不被无条件转图片；
- 重复页眉页脚显著减少；
- malformed PDF 返回稳定错误码。

### Phase 3：OpenDataLoader 与表格规范化

1. 接入本地 OpenDataLoader，引擎名 `opendataloader`。
2. 禁止访问非本机/容器内白名单地址；本期默认不启用 Hybrid 外部 Endpoint。
3. 普通表格规范化为 GFM Markdown。
4. 带 `rowspan/colspan` 且无法无损转换的表格保留 HTML，但删除样式和展示属性。
5. 为表格补充稳定的独立块边界，不在正文中泄漏临时文件路径。
6. 解析过短或失败时按配置回退 builtin PDFParser，并记录尝试链。

建议默认路由：

```yaml
document_parser:
  rules:
    pdf: builtin
  engines:
    opendataloader:
      enabled: true
      max_workers: 1
```

是否将 OpenDataLoader 设为 PDF 默认引擎，必须由基准测试决定，不在代码中硬编码。

验收：表格基准中列数、关键数字、表头和数据行可稳定恢复；输出不包含本机路径和 base64。

### Phase 4：表格感知分块

参考 WeKnora Go chunker 行为，以 Python 实现：

1. 识别 GFM Markdown table 与完整 HTML table 为 protected spans；
2. 普通分隔边界不得落在 protected span 内；
3. 表格小于上限时作为原子单元；
4. 超大表按完整行拆分；
5. 后续 Chunk 自动补正确表头；
6. 新表、正文和章节变化时正确终止表头状态；
7. 补充表头作为 `context_header` 或 metadata，不破坏正文 offset；
8. 设置最大保护长度，建议初值 7500 字符，通过基准调整。

需要扩展当前 Chunk 契约：

```python
metadata["content_type"] = "table"
metadata["context_header"] = "..."
metadata["table_index"] = 0
```

Embedding 内容为 `context_header + text`，存储正文仍保持原始 `text`，避免 offset 失真。

验收：大表跨块后每块可单独解释列含义，上一张表的表头不会污染下一张表。

### Phase 5：本地 Qwen3.8 27B 多模态入库

触发条件：

- `image_source_type == scanned_pdf`；
- 图片被分类为内容图片；
- 页面文本为空、过短或质量评分低；
- 解析结果检测到图片表格；
- 用户显式指定 `force_vision=true`。

每张图片执行：

1. OCR/结构化提示：正文、Markdown 表格、LaTeX 公式、阅读顺序；
2. Caption 提示：图表趋势、流程关系和主要视觉语义；
3. 清理拒答、提示回显、空内容和无效 OCR；
4. 分别生成 `image_ocr` 与 `image_caption` 子 Chunk；
5. 子 Chunk 记录父 Chunk、文档、页码、图片 ID 和模型版本；
6. 子 Chunk 分别进入 Embedding、BM25 和向量索引。

模型要求：

- 当前默认本地 Qwen3.8 27B；
- `supports_vision=true` 必须通过一次真实图片探测校验；
- 保留 OpenAI-compatible 本地接口；
- 配置中的旧模型名统一更新，文档与运行配置一致；
- 不实现任何外部付费 VLM Provider。

故障语义：

- 单图失败：记录 warning，其他图片继续；
- 扫描文档所有页面视觉解析失败：文档任务失败，不得将空占位符标记为成功；
- 数字 PDF 的附属图片处理失败：允许 `partial_success`，正文继续入库；
- VLM 临时不可用：可重试，使用指数退避和最大尝试次数；
- 已有 OCR/Caption 可作为聊天阶段的图片降级上下文。

### Phase 6：格式扩展

按以下顺序交付，禁止一次 PR 混入全部格式：

1. DOCX；
2. XLSX/CSV；
3. PPTX；
4. DOC/XLS/PPT 老格式；
5. TXT/HTML/MHTML；
6. EPUB/XMind；
7. 图片格式。

每种格式必须同时提交：解析器、依赖、格式路由、上传白名单、Magic/MIME 校验、测试样本和验收测试。

### Phase 7：切换默认与清理旧实现

前置条件：Phase 0 基准全部达到门槛，且至少完成一次真实业务文档灰度。

1. 默认 backend 从 `legacy` 改为 `docreader`；
2. 保留 legacy 回滚开关至少一个发布周期；
3. 确认无回滚后再删除重复 PDF/Markdown Loader；
4. 删除前必须迁移其独有的图片分类和 metadata 合同；
5. 更新 OpenAPI、README、部署文档和运维检查表。

## 9. 配置设计

建议新增：

```yaml
document_parser:
  backend: docreader
  endpoint: 127.0.0.1:50051
  request_timeout_seconds: 300
  max_file_bytes: 31457280
  default_engine: builtin
  rules:
    pdf: builtin
    docx: builtin
    xlsx: builtin
    pptx: markitdown
  fallback:
    enabled: true
    chains:
      pdf: [builtin, opendataloader]
      docx: [builtin, markitdown]
  pdf:
    force_scanned: false
    render_dpi: 200
    jpeg_quality: 85
    render_max_workers: 1
  opendataloader:
    enabled: true
    max_workers: 1
  table:
    preserve_html_spans: true
    max_protected_chars: 7500
    repeat_header_on_split: true
  multimodal:
    enabled: true
    model: Qwen3.8-27B
    supports_vision: true
    mode: on_demand
    max_workers: 1
    request_timeout_seconds: 180
    max_retries: 3
    enable_ocr: true
    enable_caption: true
```

配置加载时必须校验：超时、并发、DPI、JPEG质量、Endpoint、引擎名称和视觉模型能力。无效配置应在启动时失败，而不是解析中途才暴露。

## 10. API 与产品行为

上传接口增加可选解析覆盖参数，但默认用户不需要选择：

```json
{
  "parser_engine": "builtin",
  "force_scanned": false,
  "force_vision": false
}
```

文档详情增加：

- `parser_engine`；
- `parser_attempts`；
- `parse_status: success | partial_success | failed`；
- `page_count/text_page_count/scanned_page_count`；
- `table_count/image_count`；
- `ocr_success_count/ocr_failed_count`；
- `parse_warnings`；
- 各阶段耗时。

系统管理接口增加：

- `GET /api/v1/parser-engines`：引擎、格式、可用状态和不可用原因；
- `GET /api/v1/parser-health`：DocReader 和本地 VLM 健康状态。

本期不要求用户手工维护远程 Parser Endpoint 或云凭据。

## 11. 安全与资源限制

- 文件名必须 `basename` 化，临时目录使用系统安全 API；
- ZIP 类格式限制条目数、总解压大小、压缩比和加密条目；
- LibreOffice、antiword、Java 和其他子进程必须有超时、工作目录和退出码检查；
- 禁止从文档内容构造 Shell 命令；
- HTML/MHTML 默认不执行 JavaScript；
- 本期不开通 URL 解析；若后续开放，必须单独做 SSRF 设计和验收；
- Markdown/HTML 远程图片不得由解析服务任意下载；
- gRPC 限制收发消息大小，图片使用流式传输；
- 日志不得包含文件正文、base64、API Key 或完整敏感路径；
- 解析容器使用非 root 用户、只读根文件系统和受限临时目录；
- DocReader 不挂载向量库、BM25 或业务数据库。

## 12. 测试计划

### 12.1 直接迁移的上游回归测试

优先迁移并适配：

- `docreader/tests/test_pdf_router.py`；
- `test_pdf_embedded_images.py`；
- `test_docx_tables.py`；
- `test_docx_merge.py`；
- `test_excel_parser.py`；
- `test_ppt_convert.py`；
- `test_markdown_table_util.py`；
- `test_mhtml_parser.py`；
- `test_epub_parser.py`；
- `test_xmind_parser.py`；
- `test_parser_routing.py`；
- `test_parser_concurrency.py`；
- `test_opendataloader_parser.py`；
- `test_ssrf.py` 中仍与本地 Endpoint 限制相关的用例。

### 12.2 本项目新增测试

- DocReader Client 的正常、超时、断连、流式中断和消息过大；
- ParsedDocument 到现有 Document/Chunk 的映射；
- 图片持久化、引用重写和临时文件清理；
- Feature Flag 回滚；
- 主引擎失败后的尝试链和错误分类；
- Markdown/HTML 表格规范化；
- protected spans 与表头追踪；
- OCR/Caption 子 Chunk 父子关系；
- 视觉服务失败的 partial/failed 语义；
- 文件 Magic 与扩展名冲突；
- ZIP Bomb、路径穿越和子进程超时。

### 12.3 端到端测试

```text
上传 → DocReader → 图片保存 → 表格分块 → VLM增强
     → Embedding → BM25/向量入库 → 查询 → 引用定位
```

至少验证：

- 查询能命中表格中的精确数字；
- 大表后续 Chunk 仍能解释列名；
- 扫描页文字和图片表格可以检索；
- 图表语义能通过 Caption 检索；
- 引用返回正确文档、页码和图片；
- 删除/重解析不会遗留图片子 Chunk。

## 13. 验收门槛

以下门槛必须基于 Phase 0 固定样本，不得只用人工观感：

| 指标 | 门槛 |
| --- | --- |
| 表格关键数字准确率 | 不低于 95%，且显著高于旧实现 |
| 表格列数/表头保持率 | 不低于 95% |
| 双栏阅读顺序 | 固定样本 100% 不交错 |
| 扫描页覆盖率 | 100% 进入视觉处理或明确失败 |
| 图片 OCR/Caption 失败隔离 | 单图失败不影响其他图片 |
| 大表表头补充 | 每个数据 Chunk 100% 带正确表头上下文 |
| 本机路径/base64 泄漏 | 0 |
| 旧 PDF/Markdown 回归 | 现有测试全部通过 |
| 外部付费 API 调用 | 0 |
| 回滚能力 | 单配置切回 legacy，无数据迁移 |

性能门槛需在基准机测量后锁定。首轮建议记录 P50/P95 解析耗时、峰值内存、VLM调用次数和每页 GPU 时间，再决定硬门槛。

## 14. 可观测性

在现有 TraceContext 中新增或扩展：

- `parse.engine_selected`；
- `parse.engine_attempt`；
- `parse.engine_fallback`；
- `parse.page_classification`；
- `parse.table_normalize`；
- `parse.image_persist`；
- `parse.vision_ocr`；
- `parse.vision_caption`；
- `parse.partial_success`。

每条记录仅保存计数、耗时、引擎、模型、页码、错误码和截断后的非敏感预览。不得保存完整正文或图片数据。

## 15. 许可证与上游同步

WeKnora 根项目为 MIT，但部分 DocReader utils 带 InfiniFlow Apache-2.0 版权头。实施必须：

1. 保留每个迁移文件原有版权头；
2. 在 `services/docreader/THIRD_PARTY_NOTICES.md` 记录来源项目、文件、提交和许可证；
3. 带入必要的 MIT、Apache-2.0 许可文本；
4. 对第三方 Python、Java 和系统依赖生成依赖许可清单；
5. 不迁移未实际使用的第三方代码；
6. 每次上游同步单独提交，提交信息包含 WeKnora 来源提交；
7. `upstream` 算法层尽量少改，项目差异放入 adapters，降低以后同步成本。

## 16. PR 拆分建议

禁止用一个巨型 PR 完成本方案。建议：

1. `parser-baseline-and-provenance`
2. `docreader-core-and-contract`
3. `docreader-client-and-ingestion-adapter`
4. `weknora-pdf-parser-port`
5. `opendataloader-table-parser`
6. `table-normalization-and-protected-chunking`
7. `local-qwen-vision-ingestion`
8. `docx-parser-port`
9. `excel-parser-port`
10. `ppt-parser-port`
11. `remaining-local-formats`
12. `parser-observability-and-default-cutover`

每个 PR 必须包含：来源清单更新、单元测试、必要的集成测试、配置说明、回滚方式和基准变化。

## 17. 工程师交付清单

交付审核时需提供：

- PR/提交列表；
- 上游文件映射及改动说明；
- 第三方许可清单；
- 新增依赖和系统依赖；
- 全量测试结果；
- 固定样本新旧解析对比；
- 表格关键数字验收结果；
- P50/P95、内存和 GPU 指标；
- 外部网络访问审计结果；
- Feature Flag 回滚演示；
- 已知限制和未完成项。

## 18. 审核重点

实现完成后，代码审核重点不是“是否复制了足够多的 WeKnora 代码”，而是：

1. 迁移范围是否符合本文边界；
2. 上游算法是否被无理由重写或破坏；
3. WeKnora 专属 Go、云端和租户耦合是否被带入；
4. 图片、表格和 offset 契约是否一致；
5. 表格是否在分块后仍可正确检索；
6. 本地 Qwen3.8 27B 是否只在需要的页面/图片上调用；
7. 失败、重试、部分成功和回滚是否真实可用；
8. 是否存在付费外部 API、敏感数据外发或隐式网络下载；
9. 许可证和来源是否完整；
10. 基准数据是否证明提升，而非只增加代码和依赖。
