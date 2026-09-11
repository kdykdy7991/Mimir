import { describe, expect, it } from "vitest";
import { mcpDiagnosticGuidance } from "./mcp-diagnostics-guidance";

describe("mcpDiagnosticGuidance", () => {
  it("uses stable codes instead of parsing backend English messages", () => {
    expect(mcpDiagnosticGuidance("client_unauthorized")).toContain("MCP Client Key");
    expect(mcpDiagnosticGuidance("internal_auth_failed")).toContain("MCP_INTERNAL_API_KEY");
    expect(mcpDiagnosticGuidance("empty_scope")).toContain("至少选择一个知识库");
  });
  it("falls back to the safe server suggestion for future codes", () => {
    expect(mcpDiagnosticGuidance("future", "安全建议")).toBe("安全建议");
  });
});
