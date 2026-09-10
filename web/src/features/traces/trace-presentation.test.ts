import { describe, expect, it } from "vitest";
import type { TraceResponse } from "@/types";
import { isSkippedTrace, traceStageDescription, traceStageLabel } from "./trace-presentation";

const stage = (name: string, method?: string, details: Record<string, unknown> = {}) => ({
  name, method: method ?? null, provider: null,
  started_at: "2026-09-10T00:00:00Z", duration_ms: 1, details,
});

describe("trace presentation", () => {
  it("localizes stable and method-specific stages", () => {
    expect(traceStageLabel(stage("load"))).toBe("文档解析");
    expect(traceStageLabel(stage("transform", "image_captioner"))).toBe("图片理解");
    expect(traceStageLabel(stage("future_stage"))).toBe("其他处理步骤");
  });

  it("describes a skipped ingestion without pretending it ran", () => {
    const skipped = stage("ingestion_pipeline", undefined, { event: "skipped" });
    const trace = { stages: [skipped] } as unknown as TraceResponse;
    expect(traceStageDescription(skipped)).toBe("文件内容未变化，沿用已有解析结果");
    expect(isSkippedTrace(trace)).toBe(true);
  });
});
