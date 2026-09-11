import { ApiError, ApiNetworkError, type ApiErrorPayload } from "./error";
import type {
  BatchUploadResponse,
  CollectionCreateRequest,
  CollectionDetail,
  CollectionListResponse,
  CollectionUpdateRequest,
  MCPKeyCreateRequest,
  MCPKeyListResponse,
  MCPKeyMetadata,
  MCPKeySecretResponse,
  DocumentDetail,
  DocumentListResponse,
  DocumentUploadResponse,
  OverviewResponse,
  QueryRequest,
  QueryResponse,
  SystemHealth,
  SystemInfo,
  TaskStatusResponse,
  BatchDocumentAction,
  ActionableTraceResponse,
  BatchDocumentResponse,
  DocumentChunkDetail,
  DocumentChunkListParams,
  DocumentChunkListResponse,
  DocumentFolder,
  DocumentListFilters,
  DocumentTag,
  MCPConnectionTestResponse,
  MCPServerStatus,
  TraceListParams,
  TraceListResponse,
} from "@/types";

// Keep browser requests on the Web UI origin. Next.js proxies /api/* to the
// local FastAPI service, which avoids LAN proxy and CORS differences.
const DEFAULT_API_BASE_URL = "";
const DEFAULT_TIMEOUT_MS = 60_000;
const BATCH_UPLOAD_TIMEOUT_MS = 5 * 60_000;

type CursorParams = {
  cursor?: string | null;
  limit?: number;
};

type RequestOptions = {
  body?: BodyInit | Record<string, unknown>;
  headers?: HeadersInit;
  method?: string;
  signal?: AbortSignal | undefined;
  timeoutMs?: number;
  onResponse?: (response: Response) => void;
};

export type ApiClientOptions = {
  baseUrl?: string;
  fetch?: typeof globalThis.fetch;
  requestId?: () => string;
  timeoutMs?: number;
};

function normalizeBaseUrl(value: string) {
  return value.replace(/\/+$/, "");
}

function defaultRequestId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function appendCursorParams(path: string, params?: CursorParams) {
  if (!params) return path;
  const search = new URLSearchParams();
  if (params.cursor) search.set("cursor", params.cursor);
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

function appendQuery(path: string, params?: Record<string, unknown>) {
  if (!params) return path;
  const search = new URLSearchParams();
  for (const [key, raw] of Object.entries(params)) {
    if (raw === undefined || raw === null || raw === "") continue;
    if (Array.isArray(raw)) {
      for (const value of raw) search.append(key, String(value));
    } else {
      search.set(key, String(raw));
    }
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!isRecord(value)) return false;
  const prototype = Object.getPrototypeOf(value) as object | null;
  return prototype === Object.prototype || prototype === null;
}

async function readError(response: Response): Promise<ApiErrorPayload> {
  const fallbackRequestId = response.headers.get("X-Request-ID");
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }

  const envelope = isRecord(body) && isRecord(body.error) ? body.error : undefined;
  return {
    code: typeof envelope?.code === "string" ? envelope.code : `HTTP_${response.status}`,
    message: typeof envelope?.message === "string" ? envelope.message : response.statusText || "请求失败",
    requestId: typeof envelope?.request_id === "string" ? envelope.request_id : fallbackRequestId,
    details: isRecord(envelope?.details) ? envelope.details : {},
  };
}

export class ApiClient {
  readonly baseUrl: string;
  private readonly fetcher: typeof globalThis.fetch;
  private readonly makeRequestId: () => string;
  private readonly timeoutMs: number;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = normalizeBaseUrl(
      options.baseUrl ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? DEFAULT_API_BASE_URL,
    );
    this.fetcher = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
    this.makeRequestId = options.requestId ?? defaultRequestId;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? this.timeoutMs);
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");
    if (!headers.has("X-Request-ID")) headers.set("X-Request-ID", this.makeRequestId());

    let body = options.body;
    if (body !== undefined && isPlainRecord(body)) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(body);
    }

    const onAbort = () => controller.abort(options.signal?.reason);
    options.signal?.addEventListener("abort", onAbort, { once: true });

    try {
      const init: RequestInit = {
        headers,
        signal: controller.signal,
      };
      if (options.method !== undefined) init.method = options.method;
      if (body !== undefined) init.body = body;

      const response = await this.fetcher(`${this.baseUrl}${path}`, init);
      options.onResponse?.(response);
      if (!response.ok) throw new ApiError(response.status, await readError(response));
      if (response.status === 204) return undefined as T;
      return (await response.json()) as T;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      const message = controller.signal.aborted ? "请求已取消或超时" : "无法连接 API 服务";
      throw new ApiNetworkError(message, error);
    } finally {
      clearTimeout(timeout);
      options.signal?.removeEventListener("abort", onAbort);
    }
  }

  getSystemInfo(signal?: AbortSignal) {
    return this.request<SystemInfo>("/api/v1/system/info", { signal });
  }

  getSystemHealth(signal?: AbortSignal) {
    return this.request<SystemHealth>("/api/v1/system/health", { signal });
  }

  listCollections(params?: CursorParams, signal?: AbortSignal) {
    return this.request<CollectionListResponse>(appendCursorParams("/api/v1/collections", params), { signal });
  }

  getOverview(range: "24h" | "7d" | "30d" = "7d", signal?: AbortSignal) {
    return this.request<OverviewResponse>(`/api/v1/metrics/overview?range=${range}`, { signal });
  }

  createCollection(body: CollectionCreateRequest, signal?: AbortSignal) {
    return this.request<CollectionDetail>("/api/v1/collections", { method: "POST", body, signal });
  }

  getCollection(collectionId: string, signal?: AbortSignal) {
    return this.request<CollectionDetail>(`/api/v1/collections/${encodeURIComponent(collectionId)}`, { signal });
  }

  updateCollection(collectionId: string, body: CollectionUpdateRequest, signal?: AbortSignal) {
    return this.request<CollectionDetail>(`/api/v1/collections/${encodeURIComponent(collectionId)}`, {
      method: "PATCH", body, signal,
    });
  }

  deleteCollection(collectionId: string, signal?: AbortSignal) {
    return this.request<void>(`/api/v1/collections/${encodeURIComponent(collectionId)}`, { method: "DELETE", signal });
  }

  listMCPKeys(signal?: AbortSignal) { return this.request<MCPKeyListResponse>("/api/v1/mcp-keys", { signal }); }

  createMCPKey(body: MCPKeyCreateRequest, signal?: AbortSignal) { return this.request<MCPKeySecretResponse>("/api/v1/mcp-keys", { method: "POST", body, signal }); }

  rotateMCPKey(name: string, signal?: AbortSignal) { return this.request<MCPKeySecretResponse>(`/api/v1/mcp-keys/${encodeURIComponent(name)}/rotate`, { method: "POST", signal }); }

  renameMCPKey(name: string, newName: string, signal?: AbortSignal) { return this.request<MCPKeyMetadata>("/api/v1/mcp-keys/" + encodeURIComponent(name), { method: "PATCH", body: { name: newName }, signal }); }

  updateMCPKeyCollections(name: string, allowedCollections: string[], signal?: AbortSignal) { return this.request<MCPKeyMetadata>(`/api/v1/mcp-keys/${encodeURIComponent(name)}/collections`, { method: "PUT", body: { allowed_collections: allowedCollections }, signal }); }

  revokeMCPKey(name: string, signal?: AbortSignal) { return this.request<MCPKeyMetadata>(`/api/v1/mcp-keys/${encodeURIComponent(name)}/revoke`, { method: "POST", signal }); }

  deleteMCPKey(name: string, signal?: AbortSignal) { return this.request<void>(`/api/v1/mcp-keys/${encodeURIComponent(name)}`, { method: "DELETE", signal }); }

  listDocuments(collectionId: string, params?: CursorParams, signal?: AbortSignal) {
    const path = `/api/v1/collections/${encodeURIComponent(collectionId)}/documents`;
    return this.request<DocumentListResponse>(appendCursorParams(path, params), { signal });
  }

  listFilteredDocuments(collectionId: string, params?: DocumentListFilters, signal?: AbortSignal) {
    const path = `/api/v1/collections/${encodeURIComponent(collectionId)}/documents`;
    return this.request<DocumentListResponse>(appendQuery(path, params), { signal });
  }

  listAllDocuments(params?: CursorParams, signal?: AbortSignal) {
    // Server-side paged listing across every collection (new in the perf
    // pass) — replaces the old client that fetched every document.
    return this.request<DocumentListResponse>(appendCursorParams("/api/v1/documents", params), { signal });
  }

  uploadDocument(collectionId: string, file: File, signal?: AbortSignal) {
    const body = new FormData();
    body.set("file", file);
    return this.request<DocumentUploadResponse>(`/api/v1/collections/${encodeURIComponent(collectionId)}/documents`, {
      method: "POST",
      body,
      signal,
    });
  }

  async uploadDocumentsWithTiming(collectionId: string, files: readonly File[], signal?: AbortSignal) {
    const body = new FormData();
    for (const file of files) body.append("files", file);
    const started = typeof performance !== "undefined" ? performance.now() : Date.now();
    let serverDurationMs: number | undefined;
    let requestId: string | undefined;
    const response = await this.request<BatchUploadResponse>(`/api/v1/collections/${encodeURIComponent(collectionId)}/documents`, {
      method: "POST",
      timeoutMs: BATCH_UPLOAD_TIMEOUT_MS,
      body,
      signal,
      onResponse: (raw) => {
        const parsed = Number.parseFloat(raw.headers.get("X-Server-Duration-Ms") ?? "");
        if (Number.isFinite(parsed)) serverDurationMs = parsed;
        requestId = raw.headers.get("X-Request-ID") ?? undefined;
      },
    });
    const finished = typeof performance !== "undefined" ? performance.now() : Date.now();
    return { response, durationMs: finished - started, serverDurationMs, requestId };
  }

  async uploadDocuments(collectionId: string, files: readonly File[], signal?: AbortSignal) {
    return (await this.uploadDocumentsWithTiming(collectionId, files, signal)).response;
  }

  getDocument(documentId: string, signal?: AbortSignal) {
    return this.request<DocumentDetail>(`/api/v1/documents/${encodeURIComponent(documentId)}`, { signal });
  }


  listDocumentChunks(documentId: string, params?: DocumentChunkListParams, signal?: AbortSignal) {
    const path = `/api/v1/documents/${encodeURIComponent(documentId)}/chunks`;
    return this.request<DocumentChunkListResponse>(appendQuery(path, params), { signal }).then((response) => ({
      ...response,
      items: response.items.map((item) => ({ ...item, preview: item.text_preview ?? item.preview ?? "" })),
    }));
  }

  getDocumentChunk(documentId: string, chunkId: string, signal?: AbortSignal) {
    return this.request<DocumentChunkDetail>(`/api/v1/documents/${encodeURIComponent(documentId)}/chunks/${encodeURIComponent(chunkId)}`, { signal });
  }

  listDocumentTags(collectionId: string, signal?: AbortSignal) {
    return this.request<{ items: DocumentTag[] }>(`/api/v1/collections/${encodeURIComponent(collectionId)}/tags`, { signal }).then((value) => value.items);
  }

  createDocumentTag(collectionId: string, body: { name: string; color: string }, signal?: AbortSignal) {
    return this.request<DocumentTag>(`/api/v1/collections/${encodeURIComponent(collectionId)}/tags`, { method: "POST", body, signal });
  }

  updateDocumentTag(collectionId: string, tagId: string, body: { name?: string; color?: string }, signal?: AbortSignal) {
    return this.request<DocumentTag>(`/api/v1/collections/${encodeURIComponent(collectionId)}/tags/${encodeURIComponent(tagId)}`, { method: "PATCH", body, signal });
  }

  deleteDocumentTag(collectionId: string, tagId: string, signal?: AbortSignal) {
    return this.request<void>(`/api/v1/collections/${encodeURIComponent(collectionId)}/tags/${encodeURIComponent(tagId)}`, { method: "DELETE", signal });
  }

  replaceDocumentTags(documentId: string, tagIds: string[], signal?: AbortSignal) {
    return this.request<{ items: DocumentTag[] }>(`/api/v1/documents/${encodeURIComponent(documentId)}/tags`, { method: "PUT", body: { tag_ids: tagIds }, signal }).then((value) => value.items);
  }

  listDocumentFolders(collectionId: string, signal?: AbortSignal) {
    return this.request<{ items: DocumentFolder[] }>(`/api/v1/collections/${encodeURIComponent(collectionId)}/folders`, { signal }).then((value) => value.items);
  }

  createDocumentFolder(collectionId: string, body: { name: string; parent_id: string | null }, signal?: AbortSignal) {
    return this.request<DocumentFolder>(`/api/v1/collections/${encodeURIComponent(collectionId)}/folders`, { method: "POST", body, signal });
  }

  updateDocumentFolder(collectionId: string, folderId: string, body: { name?: string; parent_id?: string | null }, signal?: AbortSignal) {
    return this.request<DocumentFolder>(`/api/v1/collections/${encodeURIComponent(collectionId)}/folders/${encodeURIComponent(folderId)}`, { method: "PATCH", body, signal });
  }

  moveDocumentFolder(collectionId: string, folderId: string, parentId: string | null, signal?: AbortSignal) {
    return this.request<DocumentFolder>(`/api/v1/collections/${encodeURIComponent(collectionId)}/folders/${encodeURIComponent(folderId)}/move`, { method: "POST", body: { parent_id: parentId }, signal });
  }

  deleteDocumentFolder(collectionId: string, folderId: string, signal?: AbortSignal) {
    return this.request<void>(`/api/v1/collections/${encodeURIComponent(collectionId)}/folders/${encodeURIComponent(folderId)}`, { method: "DELETE", signal });
  }

  moveDocument(documentId: string, folderId: string | null, signal?: AbortSignal) {
    return this.request<void>(`/api/v1/documents/${encodeURIComponent(documentId)}/folder`, { method: "PUT", body: { folder_id: folderId }, signal });
  }

  batchTagDocuments(collectionId: string, documentIds: string[], action: BatchDocumentAction, tagIds: string[], signal?: AbortSignal) {
    return this.request<BatchDocumentResponse>(`/api/v1/collections/${encodeURIComponent(collectionId)}/documents/batch/tags`, { method: "POST", body: { document_ids: documentIds, action, tag_ids: tagIds }, signal });
  }

  batchMoveDocuments(collectionId: string, documentIds: string[], folderId: string | null, signal?: AbortSignal) {
    return this.request<BatchDocumentResponse>(`/api/v1/collections/${encodeURIComponent(collectionId)}/documents/batch/move`, { method: "POST", body: { document_ids: documentIds, folder_id: folderId }, signal });
  }

  batchReprocessDocuments(collectionId: string, documentIds: string[], signal?: AbortSignal) {
    return this.request<BatchDocumentResponse>(`/api/v1/collections/${encodeURIComponent(collectionId)}/documents/batch/reprocess`, { method: "POST", body: { document_ids: documentIds }, signal });
  }

  batchDeleteDocuments(collectionId: string, documentIds: string[], signal?: AbortSignal) {
    return this.request<BatchDocumentResponse>(`/api/v1/collections/${encodeURIComponent(collectionId)}/documents/batch/delete`, { method: "POST", body: { document_ids: documentIds }, signal });
  }

  documentPreviewUrl(documentId: string) {
    return `${this.baseUrl}/api/v1/documents/${encodeURIComponent(documentId)}/preview`;
  }

  deleteDocument(documentId: string, signal?: AbortSignal) {
    return this.request<void>(`/api/v1/documents/${encodeURIComponent(documentId)}`, { method: "DELETE", signal });
  }

  getTask(taskId: string, signal?: AbortSignal) {
    return this.request<TaskStatusResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { signal });
  }

  queryCollection(collectionId: string, body: QueryRequest, signal?: AbortSignal) {
    return this.request<QueryResponse>(`/api/v1/collections/${encodeURIComponent(collectionId)}/queries`, { method: "POST", body, signal });
  }

  getQueryTrace(queryId: string, signal?: AbortSignal) {
    return this.request<ActionableTraceResponse>(`/api/v1/queries/${encodeURIComponent(queryId)}/trace`, { signal });
  }

  getIngestionTrace(ingestionId: string, signal?: AbortSignal) {
    return this.request<ActionableTraceResponse>(`/api/v1/ingestions/${encodeURIComponent(ingestionId)}/trace`, { signal });
  }

  listTraces(params?: TraceListParams, signal?: AbortSignal) {
    return this.request<TraceListResponse>(appendQuery("/api/v1/traces", params), { signal });
  }

  retryTask(taskId: string, signal?: AbortSignal) {
    return this.request<TaskStatusResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/retry`, { method: "POST", signal });
  }

  cancelTask(taskId: string, signal?: AbortSignal) {
    return this.request<TaskStatusResponse>(`/api/v1/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST", signal });
  }

  getMCPServerStatus(signal?: AbortSignal) {
    return this.request<MCPServerStatus>("/api/v1/mcp-server/status", { signal });
  }

  testMCPConnection(apiKey: string, signal?: AbortSignal) {
    return this.request<MCPConnectionTestResponse>("/api/v1/mcp-server/test-connection", { method: "POST", body: { api_key: apiKey }, signal });
  }

  imageUrl(relativePathOrId: string) {
    const path = relativePathOrId.startsWith("/")
      ? relativePathOrId
      : `/api/v1/images/${encodeURIComponent(relativePathOrId)}`;
    return `${this.baseUrl}${path}`;
  }
}

export const apiClient = new ApiClient();
