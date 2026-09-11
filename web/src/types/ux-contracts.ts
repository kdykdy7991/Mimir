export type ChunkContentType = "text" | "table" | "image_ocr" | "image_caption";
export type SourceLocator = {
  kind: "pdf_page" | "image" | "section" | "none";
  page?: number | null;
  heading?: string | null;
};

export type DocumentChunkDetail = {
  chunk_id: string;
  document_id: string;
  index: number;
  text: string;
  heading: string | null;
  page: number | null;
  content_type: ChunkContentType;
  character_count: number;
  previous_chunk_id: string | null;
  next_chunk_id: string | null;
  source_locator: SourceLocator;
};

export type DocumentChunkListParams = {
  page?: number;
  page_size?: 20 | 50 | 100;
  q?: string;
  content_type?: ChunkContentType;
  page_number?: number;
};

export type DocumentChunkListItem = Omit<
  DocumentChunkDetail,
  "text" | "previous_chunk_id" | "next_chunk_id"
> & {
  preview: string;
  text_preview?: string;
};

export type DocumentChunkListResponse = {
  items: DocumentChunkListItem[];
  page: number;
  page_size: number;
  total: number;
  has_next: boolean;
};

export type DocumentTag = {
  id: string;
  collection_id: string;
  name: string;
  color: string;
  document_count?: number;
};

export type DocumentFolder = {
  id: string;
  collection_id: string;
  parent_id: string | null;
  name: string;
  depth: number;
  document_count: number;
};

export type DocumentListFilters = {
  cursor?: string | null;
  limit?: number;
  q?: string;
  folder_id?: string;
  tag_id?: string[];
  status?: string;
  file_type?: string;
  updated_after?: string;
  updated_before?: string;
  sort?:
    | "updated_desc"
    | "updated_asc"
    | "name_asc"
    | "name_desc"
    | "size_desc";
};

export type BatchDocumentAction = "add" | "remove" | "replace";
export type BatchDocumentResult = {
  document_id: string;
  status: "success" | "error";
  error?: { code: string; message: string } | null;
  task_id?: string | null;
};
export type BatchDocumentResponse = { items: BatchDocumentResult[] };

export type TraceListParams = {
  cursor?: string | null;
  limit?: number;
  type?: "query" | "ingestion";
  status?: string;
  collection_id?: string;
  document_id?: string;
  q?: string;
  from?: string;
  to?: string;
};
export type TraceListResponse = {
  items: import("./index").TraceResponse[];
  page_info: { next_cursor?: string | null; has_more: boolean };
};
export type ActionableTraceResponse = import("./index").TraceResponse;

export type MCPServerStatus = {
  status: "online" | "degraded" | "offline" | "misconfigured";
  mcp_url: string;
  transport: "streamable-http";
  version: string | null;
  upstream_status: "online" | "offline" | "unknown";
  checked_at: string;
  latency_ms: number | null;
};

export type MCPConnectionTestStage = {
  name: "connect" | "initialize" | "tools_list" | "list_collections";
  status: "pending" | "running" | "success" | "failed" | "skipped";
  latency_ms?: number | null;
  tool_count?: number | null;
  collection_count?: number | null;
};
export type MCPConnectionError = {
  code: string;
  message: string;
  suggested_action: string;
  request_id?: string | null;
};
export type MCPConnectionTestResponse = {
  ok: boolean;
  stages: MCPConnectionTestStage[];
  error: MCPConnectionError | null;
  tested_at: string;
};
