export const ids = {
  collection: "b2c1f0e8-1234-5678-9abc-def012345678",
  document: "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f",
  query: "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
  task: "1f2e3d4c-5b6a-4c8d-9e0f-1a2b3c4d5e6f",
};

export const collection = { id: ids.collection, name: "测试知识库", description: "MSW v0.2 fixture", document_count: 1, chunk_count: 12, created_at: "2026-08-03T00:00:00.000Z", updated_at: "2026-08-03T00:00:00.000Z" };
export const overview = {
  range: "7d", started_at: "2026-07-30T00:00:00.000Z", ended_at: "2026-08-06T00:00:00.000Z",
  status: "attention", headline: "RAG 需要关注", summary: "无结果率有所升高，建议查看相关查询。",
  query_count: 128, previous_query_count: 112, degraded_rate: 2.3, failed_ingestions: 1,
  corpus: { collection_count: 1, document_count: 1, chunk_count: 12 },
  metrics: {
    effective_retrieval_rate: { value: 92.4, previous: 90.1, target: 90 },
    p95_latency_ms: { value: 1680, previous: 1920, target: 2000 },
    no_result_rate: { value: 7.6, previous: 9.9, target: 10 },
    ingestion_success_rate: { value: 98.7, previous: 97.2, target: 99 },
  },
  traffic: { request_count: 128, previous_request_count: 112, success_rate: 99.2, average_latency_ms: 860, embedding_token_usage: null },
  retrieval_health: { success_rate: 94.5, empty_retrieval_rate: 5.5, average_top_k: 8.2, average_latency_ms: 620, rerank_success_rate: null },
  knowledge_base_health: { document_count: 1, chunk_count: 12, index_status: "ready", last_updated_at: "2026-08-06T00:00:00.000Z" },
  trend: Array.from({ length: 7 }, (_, index) => ({ timestamp: new Date(Date.UTC(2026, 6, 30 + index)).toISOString(), query_count: 16 + index, effective_retrieval_rate: 88 + index, p95_latency_ms: 1900 - index * 70 })),
  attention: [{ severity: "warning", title: "1 个文档处理失败", description: "检查失败阶段和错误信息后重新上传。", href: "/documents" }],
  knowledge_bases: [{ collection_id: ids.collection, name: "测试知识库", query_count: 128, no_result_rate: 7.6, p95_latency_ms: 1680, status: "ok" }],
};

export const document = { id: ids.document, collection_id: ids.collection, filename: "rag-guide.pdf", size_bytes: 1024, status: "ready", chunk_count: 12, image_count: 1, created_at: "2026-08-03T00:00:00.000Z", updated_at: "2026-08-03T00:00:01.000Z" };
export const queryResponse = { query_id: ids.query, answer: null, citations: [{ index: 1, chunk_id: "chunk-001", document_id: ids.document, document_name: "rag-guide.pdf", page: 2, text: "混合检索会结合语义召回与关键词召回。", scores: { dense: 0.81, sparse: 8.2, fusion: 0.031 }, images: [{ id: "img-001", url: "/api/v1/images/img-001", caption: "检索流程图" }] }], diagnostics: { duration_ms: 128, trace_id: ids.query, dense_count: 10, sparse_count: 10, fused_count: 5, reranked_count: null, degraded: false, degraded_reasons: [] } };
export const queryTrace = { id: ids.query, trace_type: "query", started_at: "2026-08-03T00:00:00.000Z", finished_at: "2026-08-03T00:00:00.128Z", total_latency_ms: 128, stages: [{ name: "dense_retrieval", method: "cosine", provider: "fixture-embedding", started_at: "2026-08-03T00:00:00.000Z", duration_ms: 60, details: { returned: 10 } }] };
export const task = { id: ids.task, document_id: ids.document, collection_id: ids.collection, status: "succeeded", progress: { stage: "upsert", percent: 100, current: 12, total: 12 }, attempt: 0, created_at: "2026-08-03T00:00:00.000Z", updated_at: "2026-08-03T00:00:01.000Z", finished_at: "2026-08-03T00:00:01.000Z" };
export const ingestionTrace = { id: ids.task, trace_type: "ingestion", started_at: "2026-08-03T00:00:00.000Z", finished_at: "2026-08-03T00:00:01.000Z", total_latency_ms: 1000, stages: [{ name: "upsert", method: "bulk", provider: "fixture-store", started_at: "2026-08-03T00:00:00.800Z", duration_ms: 200, details: { records: 12 } }] };
