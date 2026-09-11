import { buildMcpClientConfig, inferMcpServerUrl } from "./mcp-keys-view";
import { describe, expect, it } from "vitest";

describe("MCP client connection information", () => {
  it("infers the standalone MCP endpoint from the Web hostname", () => {
    expect(inferMcpServerUrl("10.0.0.8")).toBe("http://10.0.0.8:8765/mcp");
  });

  it("builds a directly usable Streamable HTTP client configuration", () => {
    const config = JSON.parse(buildMcpClientConfig("https://rag.example.com/mcp", "secret"));
    expect(config).toEqual({
      name: "skdy-knowledge-query",
      transport: "streamable-http",
      url: "https://rag.example.com/mcp",
      headers: { Authorization: "Bearer secret" },
    });
  });
});
