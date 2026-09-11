import { delay, http, HttpResponse } from "msw";
import {
  collection,
  document,
  ids,
  ingestionTrace,
  overview,
  queryResponse,
  queryTrace,
  task,
} from "./fixtures";

const api = "/api/v1";
const page = <T>(items: T[]) => ({
  items,
  page_info: { next_cursor: null, has_more: false },
});
export const handlers = [
  http.get(`${api}/system/info`, () =>
    HttpResponse.json({
      app_name: "skdy-rag-server",
      version: "0.2.0",
      storage_backend: "chroma",
      sparse_backend: "bm25",
      rerank_backend: "none",
      providers: {},
      features: {},
    }),
  ),
  http.get(`${api}/system/health`, () =>
    HttpResponse.json({ status: "ok", dependencies: [] }),
  ),
  http.get(`${api}/mcp-server/status`, () =>
    HttpResponse.json({
      status: "online",
      mcp_url: "http://localhost:8765/mcp",
      transport: "streamable-http",
      version: "0.2.0",
      upstream_status: "online",
      checked_at: "2026-09-11T03:00:00Z",
      latency_ms: 8,
    }),
  ),
  http.post(`${api}/mcp-server/test-connection`, () =>
    HttpResponse.json({
      ok: true,
      tested_at: "2026-09-11T03:00:00Z",
      error: null,
      stages: [
        { name: "connect", status: "success", latency_ms: 8 },
        { name: "initialize", status: "success", latency_ms: 12 },
        { name: "tools_list", status: "success", tool_count: 5 },
        { name: "list_collections", status: "success", collection_count: 1 },
      ],
    }),
  ),
  http.get(`${api}/collections`, () => HttpResponse.json(page([collection]))),
  http.get(`${api}/metrics/overview`, () => HttpResponse.json(overview)),
  http.post(`${api}/collections`, async ({ request }) =>
    HttpResponse.json(
      { ...collection, ...((await request.json()) as object) },
      { status: 201 },
    ),
  ),
  http.get(`${api}/collections/:id`, () => HttpResponse.json(collection)),
  http.patch(`${api}/collections/:id`, async ({ request }) =>
    HttpResponse.json({ ...collection, ...((await request.json()) as object) }),
  ),
  http.delete(
    `${api}/collections/:id`,
    () => new HttpResponse(null, { status: 204 }),
  ),
  http.get(`${api}/collections/:id/documents`, () =>
    HttpResponse.json(page([document])),
  ),
  http.get(`${api}/collections/:id/tags`, () =>
    HttpResponse.json({
      items: [
        {
          id: "tag-1",
          collection_id: ids.collection,
          name: "重要",
          color: "blue",
          document_count: 1,
        },
      ],
    }),
  ),
  http.put(`${api}/documents/:id/tags`, async ({ request }) =>
    HttpResponse.json({
      items: ((await request.json()) as { tag_ids: string[] }).tag_ids.map(
        (id) => ({
          id,
          collection_id: ids.collection,
          name: "重要",
          color: "blue",
        }),
      ),
    }),
  ),
  http.get(`${api}/collections/:id/folders`, () =>
    HttpResponse.json({ items: [] }),
  ),
  http.post(`${api}/collections/:id/documents`, () =>
    HttpResponse.json(
      {
        batch_id: "6c9f3d5a-8b1e-4a2d-9c7f-0e5d4a3b2c1a",
        collection_id: ids.collection,
        total: 1,
        accepted: 1,
        skipped: 0,
        rejected: 0,
        files: [
          {
            filename: "sample.pdf",
            document_id: ids.document,
            task_id: ids.task,
            status: "accepted",
            size_bytes: 13,
            error: null,
          },
        ],
      },
      { status: 202 },
    ),
  ),
  http.get(`${api}/documents/:id`, () =>
    HttpResponse.json({
      ...document,
      file_hash: "a1b2c3d4",
      last_task_id: ids.task,
      last_query_id: null,
      last_error: null,
      table_count: 1,
      parse_warnings: ["第 3 页存在低置信度文本"],
      parser_engine: "docreader",
      parse_status: "partial_success",
      page_count: 3,
      vision_processed: true,
      chunks: [
        {
          index: 0,
          chunk_id: "chunk-001",
          heading: "RAG 指南",
          page: 1,
          character_count: 58,
          content_type: "table",
        },
      ],
    }),
  ),
  http.get(`${api}/documents/:id/chunks`, () =>
    HttpResponse.json({
      items: [
        {
          index: 0,
          chunk_id: "chunk-001",
          document_id: ids.document,
          preview: "RAG 指南",
          heading: "RAG 指南",
          page: 1,
          character_count: 58,
          content_type: "table",
          source_locator: { kind: "pdf_page", page: 1 },
        },
      ],
      page: 1,
      page_size: 20,
      total: 1,
      has_next: false,
    }),
  ),
  http.get(`${api}/documents/:id/chunks/:chunkId`, ({ params }) =>
    HttpResponse.json({
      index: 0,
      chunk_id: String(params.chunkId),
      document_id: ids.document,
      text: "RAG 指南完整内容",
      heading: "RAG 指南",
      page: 1,
      character_count: 58,
      content_type: "table",
      previous_chunk_id: null,
      next_chunk_id: null,
      source_locator: { kind: "pdf_page", page: 1 },
    }),
  ),
  http.delete(
    `${api}/documents/:id`,
    () => new HttpResponse(null, { status: 204 }),
  ),
  http.get(`${api}/tasks/:id`, async () => {
    await delay(20);
    return HttpResponse.json(task);
  }),
  http.post(`${api}/collections/:id/queries`, () =>
    HttpResponse.json(queryResponse),
  ),
  http.get(`${api}/queries/:id/trace`, () => HttpResponse.json(queryTrace)),
  http.get(`${api}/ingestions/:id/trace`, () =>
    HttpResponse.json(ingestionTrace),
  ),
  http.get(`${api}/traces`, () =>
    HttpResponse.json({
      items: [
        { ...queryTrace, status: "success" },
        {
          ...ingestionTrace,
          status: "failed",
          error: { code: "UPSERT_FAILED", message: "写入索引失败" },
        },
      ],
      page_info: { next_cursor: null, has_more: false },
    }),
  ),
  http.get(
    `${api}/images/:id`,
    () =>
      new HttpResponse(new Uint8Array([137, 80, 78, 71]), {
        headers: { "Content-Type": "image/png" },
      }),
  ),
];
