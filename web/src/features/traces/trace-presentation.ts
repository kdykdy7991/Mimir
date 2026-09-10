import type { TraceResponse } from "@/types";

type TraceStage = NonNullable<TraceResponse["stages"]>[number];

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
  if (stage.details?.event === "skipped") return "文件内容未变化，沿用已有解析结果";
  return stage.method ?? stage.provider ?? undefined;
}

export function isSkippedTrace(trace: TraceResponse): boolean {
  return (trace.stages ?? []).some((stage) => stage.details?.event === "skipped");
}

export function shouldShowRawStageName(stage: TraceStage): boolean {
  return traceStageLabel(stage) === "其他处理步骤" || stage.name === "ingestion_pipeline";
}
