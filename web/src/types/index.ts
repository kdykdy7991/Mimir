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
