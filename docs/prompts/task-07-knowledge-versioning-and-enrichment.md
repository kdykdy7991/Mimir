# Task 07 开发契约：知识版本治理与摄取增强

> 状态：已完成
> 日期：2026-09-16
> 前置：Task 06 Gate 已通过

## 1. 交付目标与边界

为文档与 chunk 建立不可变 revision、乐观并发编辑、Diff、回滚和可恢复的局部索引激活；随后加入可独立关闭、清理和评测的标签、摘要与合成问题增强。所有写操作仅走管理 REST/Web，MCP 继续只读；回滚始终创建新 revision，不改写历史。

## 2. 现状审计

- Task 06 sidecar 已具备 document index 的 staging/active/retired 切换，可复用其“失败保留旧 active”原则，但尚无面向人工编辑的 revision 审计模型；
- `EvidenceV1` 已携带 `document_version/chunk_version`，旧引用具备版本表达基础，但精确读取尚未提供历史 revision 与 `is_current` 语义；
- Dense、BM25、parent-child 分属不同存储，局部重建必须由应用服务编排，任何一步失败都不能推进 revision 的 active 状态；
- 现有 ingestion transform 已有可选模型处理能力，但人工标签、模型建议与派生内容尚未采用隔离 namespace 和可审核 provenance。

## 3. 冻结 revision 契约

- revision 不可变，至少记录 `revision_id`、document/chunk stable ID、base revision、内容、可编辑字段、来源、原因、操作者、创建时间、状态和内容 checksum；
- 每个 chunk 同时最多一个 active revision；编辑先生成 `pending_index`，只有全部索引成功后才能原子切 active；失败 revision 保留为 `index_failed` 供审计/重试；
- 旧数据首次读取时惰性映射为 `initial` revision，不在服务启动时全库回填；同一旧内容重复读取不得生成多个 initial revision；
- 写请求必须携带 base revision（REST 同时支持 `If-Match`），与当前 active 不一致返回 409，不自动合并；字段白名单首版只允许正文与明确开放的元数据；
- 回滚以目标历史 revision 为内容创建一个新 revision，其 base 指向回滚前 active，并记录目标 revision，不重新激活旧行。

## 4. 读取、Diff 与引用语义

- 历史列表、详情、Diff 都先验证 collection/document/chunk scope；不存在与无权限同形；
- Diff 为有界行级 unified diff，限制输入正文和输出字符数，超限显式 `truncated`；
- Evidence 与精确 chunk 读取继续返回实际 `document_version/chunk_version`；读取历史引用时返回原历史内容并标记 `is_current=false`，无法保留的引用返回 `stale_reference`，禁止静默替换为当前正文。

## 5. 局部重建与增强

- 重建顺序由一个应用服务编排 Dense、BM25、parent-child 和派生索引；先写 staging，逐项校验，全部成功后切 revision active，失败保持旧 active；
- 自动标签优先匹配既有词表；模型新增只保存为 suggestion，人工与模型来源分离并记录 model/prompt/confidence/review status，绝不覆盖人工标签；
- 摘要标记 derived；合成问题使用独立 namespace，关联原 chunk/revision，可调权、关闭、删除、重建；任何命中都引用原文而非派生文本；
- 每项增强均有独立 feature flag、成本/Token/延迟统计和清理路径，默认开关由 Golden Set 决定。

## 6. 实施顺序与 Gate

1. 版本契约、SQLite repository、legacy initial 映射及存储测试；
2. 授权历史读取、详情、有界 Diff；
3. 乐观并发编辑与 pending/index_failed 状态；
4. 多索引局部重建和原子激活；
5. 回滚、MCP 历史引用语义；
6. 标签、摘要、合成问题及 UI/成本/评测。

Gate 覆盖不可变性、并发冲突、失败重建、Diff/回滚幂等、跨库隔离、历史引用、模型降级、人工标签保护、派生索引清理与 Golden Set。关闭编辑/增强后读路径仍兼容，版本历史只追加不删除。

## 7. 实施记录

- 2026-09-16：完成 07.1。新增不可变 `ChunkRevision` 契约及 SQLite `RevisionStore`；payload 与 lifecycle state 分表，支持单 active、legacy initial 惰性幂等映射、pending/index_failed/active/retired、base revision 双阶段冲突检查及同 chunk 回滚目标约束。定点测试 18 passed，compileall 与 `git diff --check` 通过。
- 2026-09-16：完成 07.2。新增逐次授权的 revision history/detail 与有界 unified diff 应用服务，并通过 3 个内部只读 HTTP 路由暴露；历史详情返回原版本正文和 `is_current`，跨 chunk/越权保持同形错误。
- 2026-09-16：完成 07.3。新增管理 REST `POST /api/v1/documents/{document_id}/chunks/{chunk_id}/revisions`；强制 `If-Match` 与 body base 一致、记录 actor/reason、限制可编辑字段，成功只创建 `pending_index`，冲突返回 409。OpenAPI 与 Web TypeScript 类型已同步；revision/API 定点 Gate 38 passed。
- 2026-09-16：完成 07.4。`RevisionIndexCoordinator` 对 participant 执行“全部 stage → 全部 validate → 逐项可补偿 activate → 最后 revision active”；任一步失败逆序 rollback/discard、标记 `index_failed` 并保留旧 active，失败 revision 可重试。已接入真实 Dense、BM25 和整文档 parent-child participant，成功后失效 collection 查询缓存；管理 REST 提供显式 rebuild/activate，失败返回 participant 信息且不推进 revision。派生索引尚未产生时不注册空 participant，后续增强启用时加入同一事务编排。
- 2026-09-16：完成 07.5 的版本与回滚主链路。回滚管理 REST 复制目标历史内容生成全新 `pending_index` revision，再复用 07.4 重建激活；不会重新激活或改写历史行。`get_chunk` 现在返回 document/chunk version 与 `is_current`，并支持 `expected_chunk_version` 断言，不匹配时显式返回 `stale_reference`；历史正文仍通过授权 revision detail 原样读取。
- 2026-09-16：完成 07.6。新增 reviewable tag suggestion，持久记录 revision、model、prompt version、confidence、审核状态/人员/时间；生成时优先映射既有词表，未知名称只作为候选，已存在的人工标签不重复建议。真实 LLM wiring 强制 JSON-only 并限制输入长度，`tag_enrichment_enabled` 默认关闭，模型故障/非法输出安全降级为空。管理 API 支持生成、列表、批准/拒绝；批准前验证同 collection 既有 tag，并通过增量 `INSERT OR IGNORE` 添加，绝不 full-replace 人工标签。服务、存储及 API 定点 Gate 20 passed。
- 2026-09-16：完成 07.7。摘要与合成问题进入独立 derived namespace，均绑定原 document/chunk/revision 及 model/prompt provenance；合成问题有独立权重，但 citation 永远映射回原 revision。`derived_content_enabled` 默认关闭；生成在 revision index coordinator 内先 staging，所有主索引成功后才写入，失败可补偿恢复旧集合。管理 API 支持列表、单项启停和按 revision 清理/重建；确定性 ID 保证相同输入幂等。
- 2026-09-16：07.8 可观测基础完成。新增 enrichment run 指标，记录 operation/model/prompt、成功状态、延迟及输入/输出字符；Token 与成本遵循 exact-or-null，Provider 不提供 usage 时保持 `null`，绝不估算为 0。新增 `/api/v1/metrics/enrichment` 汇总接口，OpenAPI/Web 类型已同步。版本/审核 UI 和增强 Golden Set 决策仍在进行中，Task 07 尚未关闭。
- 2026-09-16：完成 07.8。文档 Chunk 抽屉新增版本历史、标签审核与派生内容面板，支持批准/拒绝；未映射既有词表的候选不能直接批准。标签与派生 LLM 调用均记录 exact-or-null 成本遥测。Web Gate：typecheck、lint、107 tests、Next production build 全绿。增强默认保持关闭；关闭态 Golden Set/评测 Gate 45 passed、1 skipped，在获得独立可重复的收益数据前不批准默认开启。

## 9. 完成状态

Task 07 已完成。版本历史只追加，编辑/回滚经多索引补偿事务激活；MCP 仍为 9 个只读工具。标签与派生增强均有独立关闭、审核、清理、成本和评测路径，当前默认关闭。
