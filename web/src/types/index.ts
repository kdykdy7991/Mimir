/**
 * OpenAPI-generated contract types.
 *
 * Generated from the FastAPI v0.2 contract via ``npm run gen:types``
 * (``docs/openapi/openapi.v0.2.json`` → ``src/types/api.ts``). Do not
 * hand-edit ``api.ts`` — regenerate instead.
 *
 * Convenience aliases are re-exported here so feature code can import
 * from ``@/types`` without reaching into the generated file.
 */

export type {
  components,
  operations,
  paths,
  webhooks,
} from "./api";

import type { components } from "./api";

type Schema = components["schemas"];

export type Citation = Schema["Citation"];
export type BatchFileResult = Schema["BatchFileResult"];
export type BatchUploadResponse = Schema["BatchUploadResponse"];
export type CollectionCreateRequest = Schema["CollectionCreateRequest"];
export type CollectionUpdateRequest = Schema["CollectionUpdateRequest"];
export type CollectionDetail = Schema["CollectionDetail"];
export type CollectionListResponse = Schema["CollectionListResponse"];
export type DocumentDetail = Schema["DocumentDetail"];
export type DocumentSummary = Schema["DocumentSummary"];
export type DocumentListResponse = Schema["DocumentListResponse"];
export type DocumentUploadResponse = Schema["DocumentUploadResponse"];
export type OverviewResponse = Schema["OverviewResponse"];
export type QueryRequest = Schema["QueryRequest"];
export type QueryResponse = Schema["QueryResponse"];
export type SystemHealth = Schema["SystemHealth"];
export type SystemInfo = Schema["SystemInfo"];
export type TaskStatusResponse = Schema["TaskStatusResponse"];
export type TraceResponse = Schema["TraceResponse"];

/** Trusted-admin MCP API-key management DTOs (not OpenAPI-generated yet). */
export type MCPKeyCreateRequest = { name: string; allowed_collections: string[] };
export type MCPKeyCollectionsUpdateRequest = { allowed_collections: string[] };
export type MCPKeyMetadata = { key_id: string; name: string; allowed_collections: string[]; enabled: boolean; created_at: string; revoked_at: string | null; last_used_at: string | null };
export type MCPKeySecretResponse = MCPKeyMetadata & { api_key: string };
export type MCPKeyListResponse = { items: MCPKeyMetadata[] };

/** Data-source administration DTOs. Credentials are write-only by contract. */
export type DataSourceConnectorType = "rss" | "url";
export type DataSourceCreateRequest = {
  name: string;
  connector_type: DataSourceConnectorType;
  collection_id: string;
  policy: Record<string, unknown>;
  credentials: Record<string, unknown>;
};
export type DataSourceInfo = Omit<DataSourceCreateRequest, "credentials"> & {
  id: string;
  checkpoint: Record<string, unknown>;
  checkpoint_revision: number;
  enabled: boolean;
  created_at: number;
  updated_at: number;
};
export type DataSourceListResponse = { count: number; data_sources: DataSourceInfo[] };
export type SyncRun = {
  id: string;
  source_id: string;
  status: "running" | "succeeded" | "failed" | "conflict";
  started_at: number;
  finished_at: number | null;
  added: number;
  updated: number;
  deleted: number;
  conflicts: number;
  error_code: string | null;
};
export type SyncStatusResponse = { data_source: DataSourceInfo; last_run: SyncRun | null };
export type SyncFailuresResponse = { count: number; failures: SyncRun[] };
export type SyncConflict = {
  id: string;
  source_id: string;
  external_id: string;
  document_id: string;
  active_revision_id: string;
  last_synced_revision_id: string | null;
  remote_revision: string;
  status: "pending" | "resolved";
  created_at: number;
  resolved_at: number | null;
  resolution: string | null;
};
export type SyncConflictListResponse = { count: number; conflicts: SyncConflict[] };
export type DataSourceConnectionTestResponse = { ok: boolean; data_source_id: string };
export type DataSourceSyncEnqueueResponse = { task_id: string; enqueued: boolean };
export type SyncDeadLetter = {
  task_id: string;
  queue: "sync";
  state: "dead_letter";
  attempt: number;
  max_attempts: number;
  error_class: string | null;
  error_code: string | null;
  created_at: number;
  updated_at: number;
};
export type SyncDeadLetterListResponse = { count: number; dead_letters: SyncDeadLetter[] };
export type SyncDeadLetterReplayResponse = { task_id: string; replayed: boolean };

// Transitional aliases for the frozen WeKnora UX contract. Move these into
// generated api.ts once the matching backend OpenAPI document lands.
export type {
  BatchDocumentAction,
  ActionableTraceResponse,
  BatchDocumentResult,
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
  RevisionSummary,
  TagSuggestion,
  DerivedArtifact,
} from "./ux-contracts";
