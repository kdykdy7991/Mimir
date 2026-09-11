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
} from "./ux-contracts";
