import { expect, test, type Page } from "@playwright/test";

const ids = { collection: "b2c1f0e8-1234-5678-9abc-def012345678", document: "7d4a1c3e-2b0a-4d2c-8e9f-1a2b3c4d5e6f", query: "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d", task: "1f2e3d4c-5b6a-4c8d-9e0f-1a2b3c4d5e6f" };
const collection = { id: ids.collection, name: "E2E 知识库", description: "Playwright fixture", document_count: 1, chunk_count: 12, created_at: "2026-08-03T00:00:00.000Z", updated_at: "2026-08-03T00:00:00.000Z" };
const document = { id: ids.document, collection_id: ids.collection, filename: "rag-guide.pdf", size_bytes: 1024, status: "ready", chunk_count: 12, image_count: 0, created_at: "2026-08-03T00:00:00.000Z", updated_at: "2026-08-03T00:00:01.000Z" };
const pageOf = (items: unknown[]) => ({ items, page_info: { next_cursor: null, has_more: false } });
const overview = {
  range: "7d", started_at: "2026-07-30T00:00:00Z", ended_at: "2026-08-06T00:00:00Z",
  status: "attention", headline: "RAG 需要关注", summary: "1 个文档处理失败，建议优先处理。",
  query_count: 128, previous_query_count: 112, degraded_rate: 2.3, failed_ingestions: 1,
  corpus: { collection_count: 1, document_count: 1, chunk_count: 12 },
  metrics: {
    effective_retrieval_rate: { value: 92.4, previous: 90.1, target: 90 },
    p95_latency_ms: { value: 1680, previous: 1920, target: 2000 },
    no_result_rate: { value: 7.6, previous: 9.9, target: 10 },
    ingestion_success_rate: { value: 98.7, previous: 97.2, target: 99 },
  },
  traffic: { request_count: 128, previous_request_count: 112, success_rate: 99.2, average_latency_ms: 860, token_usage: null },
  retrieval_health: { success_rate: 94.5, empty_retrieval_rate: 5.5, average_top_k: 8.2, average_latency_ms: 620, rerank_success_rate: null },
  knowledge_base_health: { document_count: 1, chunk_count: 12, index_status: "ready", last_updated_at: "2026-08-06T00:00:00Z" },
  trend: Array.from({ length: 7 }, (_, index) => ({ timestamp: new Date(Date.UTC(2026, 6, 30 + index)).toISOString(), query_count: 16 + index, effective_retrieval_rate: 88 + index, p95_latency_ms: 1900 - index * 70 })),
  attention: [{ severity: "warning", title: "1 个文档处理失败", description: "检查失败阶段和错误信息后重新上传。", href: "/documents" }],
  knowledge_bases: [{ collection_id: ids.collection, name: collection.name, query_count: 128, no_result_rate: 7.6, p95_latency_ms: 1680, status: "ok" }],
};


async function mockApi(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname; const method = request.method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/v1/system/info") return json({ app_name: "skdy-rag-server", version: "0.2.0", storage_backend: "chroma", sparse_backend: "bm25", rerank_backend: "none", providers: {}, features: {} });
    if (path === "/api/v1/system/health") return json({ status: "ok", dependencies: [] });
    if (path === "/api/v1/collections" && method === "GET") return json(pageOf([collection]));
    if (path === "/api/v1/collections" && method === "POST") return json({ ...collection, ...(request.postDataJSON() as object) }, 201);
    if (path === "/api/v1/metrics/overview") return json({ ...overview, range: url.searchParams.get("range") ?? "7d" });
    if (path === `/api/v1/collections/${ids.collection}` && method === "GET") return json(collection);
    if (path === `/api/v1/collections/${ids.collection}/documents` && method === "GET") return json(pageOf([document]));
    if (path === `/api/v1/collections/${ids.collection}` && method === "PATCH") return json({ ...collection, ...(request.postDataJSON() as object), updated_at: "2026-08-06T01:00:00.000Z" });
    if (path === `/api/v1/collections/${ids.collection}/documents` && method === "POST") return json({ batch_id: "6c9f3d5a-8b1e-4a2d-9c7f-0e5d4a3b2c1a", collection_id: ids.collection, total: 1, accepted: 1, skipped: 0, rejected: 0, files: [{ filename: "sample.pdf", document_id: ids.document, task_id: ids.task, status: "accepted", size_bytes: 13, error: null }] }, 202);
    if (path === `/api/v1/tasks/${ids.task}`) return json({ id: ids.task, document_id: ids.document, collection_id: ids.collection, status: "succeeded", progress: { stage: "upsert", percent: 100 }, attempt: 0, created_at: "2026-08-03T00:00:00Z", updated_at: "2026-08-03T00:00:01Z" });
    if (path.endsWith("/queries") && method === "POST") return json({ query_id: ids.query, answer: null, citations: [{ index: 1, chunk_id: "chunk-1", document_id: ids.document, document_name: "rag-guide.pdf", page: 2, text: "混合检索结合语义与关键词召回。", scores: { fusion: 0.031 }, images: [] }], diagnostics: { duration_ms: 128, trace_id: ids.query, degraded: false, degraded_reasons: [] } });
    if (path === `/api/v1/queries/${ids.query}/trace`) return json({ id: ids.query, trace_type: "query", started_at: "2026-08-03T00:00:00Z", finished_at: "2026-08-03T00:00:00.128Z", total_latency_ms: 128, stages: [{ name: "fusion", method: "rrf", provider: "rrf", started_at: "2026-08-03T00:00:00Z", duration_ms: 2, details: { output_count: 1 } }] });
    if (path === `/api/v1/ingestions/${ids.task}/trace`) return json({ id: ids.task, trace_type: "ingestion", started_at: "2026-08-03T00:00:00Z", finished_at: "2026-08-03T00:00:01Z", total_latency_ms: 1000, stages: [{ name: "upsert", started_at: "2026-08-03T00:00:00Z", duration_ms: 200, details: {} }] });
    return json({ error: { code: "NOT_FOUND", message: "mock route missing", request_id: "e2e", details: { path } } }, 404);
  });
}

test.beforeEach(async ({ page }) => mockApi(page));

test("overview and collection data render from API", async ({ page }) => {
  await page.goto("/overview");
  await expect(page.getByRole("heading", { name: "运行总览" })).toBeVisible();
  await expect(page.getByText("Traffic")).toBeVisible();
  await expect(page.getByText("请求量")).toBeVisible();
  await expect(page.getByText("99.2%")).toBeVisible();
  await expect(page.getByText("请求趋势")).toBeVisible();
  await expect(page.getByText("检索健康度")).toBeVisible();
  await expect(page.getByRole("heading", { name: "知识库状态" })).toBeVisible();
  await expect(page.getByText("未接入", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "知识库", exact: true }).click();
  await expect(page.getByRole("heading", { name: "E2E 知识库" })).toBeVisible();
});

test("legacy overview response does not crash the page", async ({ page }) => {
  await page.route("**/api/v1/metrics/overview**", async (route) => {
    const legacy = { ...overview, traffic: undefined, retrieval_health: undefined, knowledge_base_health: undefined };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(legacy) });
  });
  await page.goto("/overview");
  await expect(page.getByRole("heading", { name: "运行总览" })).toBeVisible();
  await expect(page.getByText("后端仍在返回旧版指标，重启后端后将显示完整数据。")).toBeVisible();
  await expect(page.getByText("状态未提供")).toBeVisible();
});

test("query → citation → trace main path", async ({ page }) => {
  await page.goto("/playground");
  await expect(page.getByRole("option", { name: "E2E 知识库" })).toBeAttached();
  await page.getByLabel("向知识库检索").fill("什么是混合检索？");
  await page.getByRole("button", { name: "运行检索" }).click();
  await expect(page.getByText("混合检索结合语义与关键词召回。")).toBeVisible();
  await page.getByRole("link", { name: /查看完整 Trace/ }).click();
  await expect(page.getByRole("heading", { name: "查询链路" })).toBeVisible();
  await expect(page.getByText("fusion", { exact: true })).toBeVisible();
});

test("collection description can be edited", async ({ page }) => {
  let currentDescription = collection.description;
  await page.route(`**/api/v1/collections/${ids.collection}`, async (route) => {
    const method = route.request().method();
    if (method === "PATCH") {
      currentDescription = (route.request().postDataJSON() as { description: string }).description;
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...collection, description: currentDescription }) });
    }
    if (method === "GET") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...collection, description: currentDescription }) });
    return route.fallback();
  });

  await page.goto(`/collections/${ids.collection}`);
  await page.getByRole("button", { name: "编辑描述" }).click();
  const description = page.getByLabel("描述");
  await expect(description).toHaveValue("Playwright fixture");
  await description.fill("更新后的知识库描述");
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(page.locator("header").getByText("更新后的知识库描述")).toBeVisible();
});

test("upload → task → ingestion trace main path", async ({ page }) => {
  await page.goto(`/collections/${ids.collection}`);
  await page.getByRole("button", { name: "上传文件" }).click();
  await page.getByLabel("选择文件").setInputFiles({ name: "sample.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 mock") });
  await page.getByRole("button", { name: "上传并处理" }).click();
  await page.getByRole("button", { name: "完成" }).click();
  await expect(page.getByText("共 1 份，已处理 1 份")).toBeVisible();
  await page.getByText("查看各文档处理记录").click();
  await expect(page.getByText("sample.pdf")).toBeVisible();
  await expect(page.getByText("100% · upsert")).toBeVisible();
  await page.getByRole("link", { name: "查看 Trace" }).click();
  await expect(page.getByRole("heading", { name: "文档处理链路" })).toBeVisible();
});

test("failed upload can be retried", async ({ page }) => {
  let attempts = 0;
  await page.route(`**/api/v1/collections/${ids.collection}/documents`, async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    attempts += 1;
    if (attempts === 1) {
      return route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "UPLOAD_FAILED", message: "mock failure", request_id: "e2e-retry", details: {} } }) });
    }
    return route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ batch_id: "6c9f3d5a-8b1e-4a2d-9c7f-0e5d4a3b2c1a", collection_id: ids.collection, total: 1, accepted: 1, skipped: 0, rejected: 0, files: [{ filename: "retry.pdf", document_id: ids.document, task_id: ids.task, status: "accepted", size_bytes: 13, error: null }] }) });
  });

  await page.goto(`/collections/${ids.collection}`);
  await page.getByRole("button", { name: "上传文件" }).click();
  await page.getByLabel("选择文件").setInputFiles({ name: "retry.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 retry") });
  await page.getByRole("button", { name: "上传并处理" }).click();

  const retry = page.getByRole("button", { name: "重试上传（1）" });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect(page.getByText("已提交处理")).toBeVisible();
  expect(attempts).toBe(2);
});
