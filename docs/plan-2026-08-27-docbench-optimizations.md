# RAG 检索质量优化路线图（基于 docbench 2026-08-27）

> 状态：**暂缓，未启动**。本文档记录从 docbench 实证 + PDF 管线代码调研导出的优化方向，
> 留作后续排期。重跑 docbench 时按 Tier 顺序逐项验证。

## 0. 背景与来源

- **docbench 实证**：`~/下载/server/docbench_RAG检索质量分析_20260827.docx`（31 KB，单作者）。
  三题答案全对，但都是单栏文档 + 概念/数字题，恰好避开了表格/图的核心证据。
- **代码调研**：同期对 `src/libs/loader/pdf_loader.py` / `document_chunker.py` / splitter /
  embedding / retrieval 的端到端扫描（结论在调研输出里，这里只引用结果）。
- **交集**：docbench 报的 7 个问题中 4 个直接坐实代码层结论；3 个（state_db 去重、跨文档
  噪声、doc_id 不对齐）只在 agent/state 层可见，PDF 抽取代码里看不到。

## 1. docbench 问题清单（原文摘录 + 影响）

| # | 问题 | 证据 | 影响 | 建议（docbench 原文） |
|---|---|---|---|---|
| 1 | 双栏 PDF 按行交错抽取 | db_0008 Context 4/8/10 左栏插入右栏 | 证据句碎片化，agent 多次检索拼合，token/时延 ×1.5~2 | 入库时按 x 坐标分栏、分栏提取后再拼接 |
| 2 | 勾选/汇总类表格结构丢失 | db_0001 Table 2：✓ 游离、Total 行缺失 | 表格题（按表格数字作答）纯文本不可解 | 表格结构化抽取，或页面渲染 + 视觉兜底 |
| 3 | 栅格图仅存占位符 | `[IMAGE: ...]` 在 chunk 文本中 | 图中数值（F-score 等）全部丢失 | 关键图区域 OCR/视觉描述回填 chunk |
| 4 | MEDIA 本机路径泄漏 | chunk 内出现 `C:\Users\jish\...\img_*.png` | 跨机器复现成死字符串，污染检索文本 | 入库清洗时过滤/替换 MEDIA 行 |
| 5 | `get_document_summary` doc_id 不对齐 | `doc_id='db_0008'` 返回 document not found | agent 补救调用失败，浪费一轮 | 核对摘要索引与检索索引的 doc_id 映射 |
| 6 | 跨文档噪声混入 | db_text_0017 top-5 含 db_0005（脚本）、db_0006（URL） | 2/5 上下文为纯噪声 | 集合按文档建子库或支持 doc_id 过滤 |
| 7 | 重复 chunk 未去重 | db_text_0003 十 context 中 3 组近似重复 | 浪费 context 窗口 | state_db 拼接前做 chunk 级去重 |

docbench 报告的修复优先级：**问题 1 > 问题 2/3 > 问题 4**。
本次路线图按 ROI（影响 × 实施成本）重排，分为三档。

## 2. Tier 1：低垂果实（建议 1~2 天内集中完成）

### Q1. 修 MEDIA 路径泄漏

- **现状**：`PdfLoader._process_page` 拼装 payload 时未过滤 `MEDIA:` 开头的项，绝对路径直接落进 chunk 文本。
- **落点**：`src/libs/loader/pdf_loader.py:_process_page`，在 merge 前丢弃以 `MEDIA:` 开头的 payload；或 `ChunkRefiner` 加一行 regex strip（`^MEDIA:\S+`）。
- **风险**：低；只会让原来就不该入索引的噪声消失。
- **验收**：docbench 重跑时 grep chunk text 应 0 命中 `MEDIA:`。

### Q2. 重复 chunk 去重

- **现状**：agent `state_db` 拼接 context 时直接把多轮检索的 chunk 累加，未做内容级去重。
- **落点**：state_db 拼接前对每个 chunk 算 `(doc_id, text[:80])` 哈希，重复项只保留第一次出现。
- **风险**：低；保留的总是更早被检索到的 chunk，不影响召回顺序。
- **验收**：db_text_0003 类题目 context 数量明显下降，token 消耗下降。

### Q3. 修复 `get_document_summary` doc_id 不对齐

- **现状**：摘要索引与检索索引的 doc_id 映射不一致。
- **落点**：MCP `get_document_summary` 工具的实现层（待定位），统一两套索引的 doc_id 来源。
- **风险**：低；改的是查询路径。
- **验收**：db_0008 调用能正确返回摘要。

## 3. Tier 2：核心收益（做完 docbench 重跑大概率掉头）

### Q4. 双栏 PDF 分栏抽取

- **现状**：`pdf_loader.py:140-152, 213-220` 对所有 line 直接 `(y, x)` 排序合并——双栏页面共享 y 时左右栏逐行交错。
- **方案**：
  1. 收集页面所有 text-line 的 `bx0`（左 x 坐标），做 1-D 聚类（KMeans 或简单 gap-based，阈值建议 ≥ page_width/4 的 gap 才算列边界）；
  2. 同列内按 y 排序；
  3. 多列按左→右 concat，列间插 `\n\n`；
  4. **降级保险**：只对 ≥ 2 个明显 x-cluster 的页面分栏；单栏页面走原逻辑，零误伤。
- **陷阱**：
  - 列边界 gap 必须**大于**段首缩进的常见范围（4~20 pt），否则会把段落缩进误判为列分隔；
  - 建议用 median(x) + IQR 做 robust 聚类，而不是固定阈值。
- **落点**：`src/libs/loader/pdf_loader.py:_process_page`，在 merge 前插一个 column detection 步骤。
- **预期收益**：db_0008 类题 token 消耗从 65k 砍到接近单栏的 41k；agent 不再做二次检索。
- **验收**：db_0008 Context 中不再出现左/右栏交错现象；docbench 三题 token/时延回到单栏基线。

### Q5. 表格最小可用方案（先解"勾选+汇总"档）

- **现状**：`find_tables` / `extract_tables` 全仓 0 命中，PyMuPDF 已支持但未启用；splitter 按字符切可能切碎表格；`TableRef` / `TableContent` 块不存在（`content_block.py:20` 显式声明"等真实用例再加"）。
- **方案分两层**：

  **Q5a（必须做）：PyMuPDF `find_tables()` + 不可切分块**
  1. `PdfLoader._process_page` 调 `page.find_tables()`，对返回的每个表用 `.to_markdown()` 转成 markdown；
  2. 把 markdown table 整块注入 page text，**用 sentinel 标记**（如 `\n[TABLE_BLOCK]\n... \n[/TABLE_BLOCK]\n`）围起来；
  3. `recursive_splitter` 加 `unsplittable_blocks` 支持：扫描 sentinel 对之间的内容，整块保留不切；
  4. `DocumentChunker` 接收 unsplittable blocks 后单独 emit chunk（chunk_size 单独配，绕过 1024 上限）。

  **Q5b（暂缓）：`TableRef` dataclass + `TableContent` block**
  - 按 `content_block.py:20` 的设计原则等真实用例再加；Q5a 让表当特殊 markdown 文本处理即可，M2 后端不需要新 schema。

- **陷阱**：
  - `find_tables()` 对没有视觉边框的表会漏检；如果 docbench 命中的是这类表，需要降级到启发式（多列对齐 + 重复行模式）；
  - 单表 chunk 可能上千字，要在 `core/settings.py:SplitterSettings` 给"表格块"单独 `chunk_size`，默认 4096 起步。
- **落点**：`pdf_loader.py` / `recursive_splitter.py` / `document_chunker.py` / `core/settings.py`。
- **预期收益**：db_0001 Table 2 类"勾选+汇总"题变可解；Total 行不再丢失。
- **验收**：db_0001 Table 2 chunk 包含 Total 行；按表作答的题目能命中正确数字。

### Q6（可选，不在 Tier 2 必须）：OCR/视觉兜底

- 当前 chunk 里图片只有 `[IMAGE: id]` 占位符，图的数值（柱状图高度、F-score 数字等）完全丢失。
- 三选一：
  1. 离线 OCR（pytesseract）→ 把 OCR 文本回填到 chunk；
  2. 关键图额外产出 `ImageCaption` block（已有 `ImageContent` 字段，加 caption 即可）；
  3. 多模态 VLM 描述（成本最高，最准）。
- 建议先做 (1) 或 (2)，(3) 看调用预算。
- docbench 优先级低（docbench 三题都没落在图数值上），所以**不一定做**。

## 4. Tier 3：架构层（先观察 docbench 重跑结果再决定）

### Q7. 跨文档噪声 → 集合级子库或 doc_id 过滤

- **现象**：db_text_0017 top-5 含 db_0005（tokenizer 脚本）、db_0006（URL），2/5 上下文为纯噪声。
- **两条路**：
  - **轻量**：检索阶段加 `where={"doc_id": {"$in": allowed}}`（Chroma 已支持）；
  - **重量**：按 collection 拆库（已有 `loader_factory` 这个抽象点可用）。
- **决策依赖**：产品形态——用户是按文档集检索，还是单文档深挖？
  - 前者 → 拆库；
  - 后者 → doc_id 过滤。

## 5. 建议执行顺序（与建议一起重跑 docbench 的节奏）

```
Week N:
  Day 1 (上午):  Q1 MEDIA 路径泄漏   ─┐
  Day 1 (下午):  Q2 state_db 去重     ─┼─→ Tier 1 完成，commit
  Day 1 (晚上):  Q3 doc_id 对齐       ─┘
  Day 2:         Q4 双栏分栏抽取        ──→ Tier 2 上半
  Day 3~4:       Q5a 表格 find_tables    ──→ Tier 2 下半
  Day 5:         重跑 docbench,  对比基线
```

不进入 Tier 3 的 Q6/Q7，先看 Tier 2 重跑结果再排。

## 6. 待决 / 开放问题

1. **Q4 列检测阈值**：是用全局阈值（page_width/4 gap）还是 IQR 自适应？前者简单可解释，
   后者更 robust。倾向先用前者跑 baseline，对照 docbench 误判率再决定。
2. **Q5a 单表 chunk_size 上限**：4096 是猜测，需要在 docbench 数据上观察实际表大小分布再定。
3. **Q7 子库 vs 过滤**：等前端需求明确再定，不预先设计。
4. **未做的 docbench baseline 记录**：本次 docbench 的 token/时延数字没有存到任何地方，
   建议下次重跑前先把当前数字落到 `docs/baselines/` 或类似目录，作为对照。

## 7. 关联

- `~/下载/server/docbench_RAG检索质量分析_20260827.docx` — 原始分析报告
- PDF 管线代码调研 — 结论见本次会话前半部分（`pdf_loader.py:140-152, 213-220` /
  `document_chunker.py` / `recursive_splitter.py` / `content_block.py:20` 的引用）
- `docs/plan-2026-07-31-m2-batch3.md` — 旧 plan 格式参考
