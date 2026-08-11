import { describe, expect, it } from "vitest";

import { calculateBatchProgress } from "./upload-batch-progress";

describe("calculateBatchProgress", () => {
  it("keeps the selected file count as the denominator before all uploads return", () => {
    expect(calculateBatchProgress(20, 0, [100, 100, 50])).toEqual({
      completed: 2,
      percent: 13,
    });
  });

  it("counts skipped, rejected, or failed uploads as settled without a task", () => {
    expect(calculateBatchProgress(4, 2, [100, 40])).toEqual({
      completed: 3,
      percent: 85,
    });
  });
});
