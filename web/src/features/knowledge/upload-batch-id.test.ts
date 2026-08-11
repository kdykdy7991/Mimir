import { describe, expect, it } from "vitest";

import { createUploadBatchId } from "./upload-batch-id";

describe("createUploadBatchId", () => {
  it("creates distinct ids without requiring Web Crypto", () => {
    const first = createUploadBatchId(1_000);
    const second = createUploadBatchId(1_000);

    expect(first).toMatch(/^upload-/);
    expect(second).not.toBe(first);
  });
});
