import type { TraceResponse } from "@/types";

type TraceStage = NonNullable<TraceResponse["stages"]>[number];
export type TraceStatus = "pending" | "running" | "success" | "warning" | "failed" | "skipped" | "canceled";

export const TRACE_STATUS_PRESENTATION: Record<TraceStatus, { label: string; description: string }> = {
  pending: { label: "等待中", description: "任务正在等待执行" },
  running: { label: "处理中", description: "任务正在执行当前阶段" },
  success: { label: "已完成", description: "所有必要阶段均已完成" },
  warning: { label: "已完成但有警告", description: "结果可用，但部分阶段需要检查" },
  failed: { label: "失败", description: "任务未能完成" },
  skipped: { label: "已跳过", description: "该阶段无需执行" },
  canceled: { label: "已取消", description: "任务已按请求停止" },
};

export function normalizeTraceStatus(value: unknown): TraceStatus {
  if (typeof value === "string" && value in TRACE_STATUS_PRESENTATION) return value as TraceStatus;
  if (value === "processing") return "running";
  if (value === "succeeded" || value === "ready" || value === "ok") return "success";
  if (value === "cancelled") return "canceled";
  return "pending";
}

const STAGE_LABELS: Record<string, string> = {
  load: "文档解析",
  split: "内容分块",
  embed: "向量化",
  upsert: "索引写入",
  ingestion_pipeline: "重复文件检查",
  query_processing: "查询预处理",
  dense: "语义检索",
  dense_retrieval: "语义检索",
  sparse: "关键词检索",
  sparse_retrieval: "关键词检索",
  fusion: "检索结果融合",
  rerank: "相关性重排",
  trim: "上下文裁剪",
};

const TRANSFORM_LABELS: Record<string, string> = {
  chunk_refiner: "内容清理与优化",
  image_classifier: "图片识别",
  image_captioner: "图片理解",
  vision_ingest: "多模态识别",
  metadata_enricher: "元数据补充",
};

export function traceStageLabel(stage: TraceStage): string {
  if (stage.name === "transform") {
    return TRANSFORM_LABELS[stage.method ?? ""] ?? "内容增强";
  }
  return STAGE_LABELS[stage.name] ?? "其他处理步骤";
}

export function traceStageDescription(stage: TraceStage): string | undefined {
  if (stage.details?.event === "skipped") return typeof stage.details.skip_reason === "string" ? stage.details.skip_reason : "文件内容未变化，沿用已有解析结果";
  return stage.method ?? stage.provider ?? undefined;
}

export function isSkippedTrace(trace: TraceResponse): boolean {
  return (trace.stages ?? []).some((stage) => stage.details?.event === "skipped");
}

export function shouldShowRawStageName(stage: TraceStage): boolean {
  return traceStageLabel(stage) === "其他处理步骤" || stage.name === "ingestion_pipeline";
}
