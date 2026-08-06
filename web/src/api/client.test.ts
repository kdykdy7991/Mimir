import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "./client";
import { ApiError, ApiNetworkError } from "./error";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
  });
}

describe("ApiClient", () => {
  it("uses same-origin API paths by default", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({}));
    const client = new ApiClient({ fetch: fetcher });

    await client.getSystemInfo();

    expect(fetcher.mock.calls[0]![0]).toBe("/api/v1/system/info");
  });

  it("uses the configured base URL and forwards a request ID", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ name: "SKDY" }));
    const client = new ApiClient({ baseUrl: "http://api.test/", fetch: fetcher, requestId: () => "request-123" });

    await client.getSystemInfo();

    expect(fetcher).toHaveBeenCalledOnce();
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("http://api.test/api/v1/system/info");
    expect(new Headers(init?.headers).get("X-Request-ID")).toBe("request-123");
  });

  it("serializes opaque cursor pagination parameters", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ items: [], page_info: { next_cursor: null, has_more: false } }));
    const client = new ApiClient({ baseUrl: "http://api.test", fetch: fetcher });

    await client.listCollections({ cursor: "next/+==", limit: 20 });

    expect(fetcher.mock.calls[0]![0]).toBe("http://api.test/api/v1/collections?cursor=next%2F%2B%3D%3D&limit=20");
  });

  it("maps the stable error envelope to ApiError", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ error: { code: "COLLECTION_NOT_FOUND", message: "Not found", request_id: "req-404", details: { collection_id: "missing" } } }, { status: 404 }));
    const client = new ApiClient({ fetch: fetcher });

    const error = await client.getCollection("missing").catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, code: "COLLECTION_NOT_FOUND", requestId: "req-404", details: { collection_id: "missing" } });
  });

  it("does not set a multipart content type for uploads", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ document: {}, task_id: "task-id" }, { status: 202 }));
    const client = new ApiClient({ fetch: fetcher });
    const file = new File(["pdf"], "sample.pdf", { type: "application/pdf" });

    await client.uploadDocument("collection-id", file);

    const [, init] = fetcher.mock.calls[0]!;
    expect(init?.body).toBeInstanceOf(FormData);
    expect(new Headers(init?.headers).has("Content-Type")).toBe(false);
  });

  it("uploads a batch independently so one rejected file does not block the rest", async () => {
    const response = {
      batch_id: "batch-1",
      collection_id: "collection-id",
      total: 2,
      accepted: 1,
      skipped: 0,
      rejected: 1,
      files: [
        { filename: "guide.pdf", document_id: "document-1", task_id: "task-1", status: "accepted", size_bytes: 3, error: null },
        { filename: "notes.md", document_id: null, task_id: null, status: "rejected", size_bytes: 7, error: { code: "INVALID_FILE", message: "Invalid file", details: {} } },
      ],
    };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(response, { status: 202 }));
    const client = new ApiClient({ fetch: fetcher });
    const files = [
      new File(["pdf"], "guide.pdf", { type: "application/pdf" }),
      new File(["# notes"], "notes.md", { type: "text/markdown" }),
    ];

    const results = await client.uploadDocuments("collection-id", files);

    expect(results).toEqual(response);
    expect(fetcher).toHaveBeenCalledOnce();
    const [, init] = fetcher.mock.calls[0]!;
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).getAll("files")).toEqual(files);
    expect((init?.body as FormData).has("file")).toBe(false);
    expect(new Headers(init?.headers).has("Content-Type")).toBe(false);
  });

  it("wraps transport failures without exposing fetch implementation details", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("connection refused"));
    const client = new ApiClient({ fetch: fetcher });

    await expect(client.getSystemHealth()).rejects.toBeInstanceOf(ApiNetworkError);
  });

  it("resolves controlled image URLs against the API origin", () => {
    const client = new ApiClient({ baseUrl: "http://api.test/" });
    expect(client.imageUrl("img/unsafe")).toBe("http://api.test/api/v1/images/img%2Funsafe");
    expect(client.imageUrl("/api/v1/images/img-001")).toBe("http://api.test/api/v1/images/img-001");
  });

  it("sends the frozen v0.2 query request and reads query traces", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ query_id: "query-id", citations: [], diagnostics: { duration_ms: 12 } }))
      .mockResolvedValueOnce(jsonResponse({ id: "query-id", trace_type: "query", started_at: "2026-08-03T00:00:00Z", finished_at: "2026-08-03T00:00:00Z", total_latency_ms: 12, stages: [] }));
    const client = new ApiClient({ baseUrl: "http://api.test", fetch: fetcher });

    await client.queryCollection("collection-id", { query: "vector search", top_k: 8, mode: "hybrid", enable_rerank: false });
    await client.getQueryTrace("query-id");

    expect(fetcher.mock.calls[0]![0]).toBe("http://api.test/api/v1/collections/collection-id/queries");
    expect(fetcher.mock.calls[0]![1]?.body).toBe(JSON.stringify({ query: "vector search", top_k: 8, mode: "hybrid", enable_rerank: false }));
    expect(fetcher.mock.calls[1]![0]).toBe("http://api.test/api/v1/queries/query-id/trace");
  });

  it("uses the task ID for ingestion traces", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ id: "task-id", trace_type: "ingestion", started_at: "2026-08-03T00:00:00Z", finished_at: "2026-08-03T00:00:00Z", total_latency_ms: 20, stages: [] }));
    const client = new ApiClient({ baseUrl: "http://api.test", fetch: fetcher });

    await client.getIngestionTrace("task-id");

    expect(fetcher.mock.calls[0]![0]).toBe("http://api.test/api/v1/ingestions/task-id/trace");
  });
});
