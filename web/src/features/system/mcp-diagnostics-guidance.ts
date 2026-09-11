const GUIDANCE: Record<string, string> = {
  server_unreachable: "检查 MCP 容器是否运行，以及管理台配置的公开 MCP URL 是否可达。",
  handshake_failed: "确认服务端与客户端使用兼容的 Streamable HTTP MCP 协议版本。",
  client_unauthorized: "确认使用的是 MCP Client Key，而不是 MCP_INTERNAL_API_KEY。",
  client_key_revoked: "该 Key 已撤销，请创建或轮换 Key 后更新客户端配置。",
  internal_auth_failed: "检查 API 与 MCP 容器注入的 MCP_INTERNAL_API_KEY 是否完全一致。",
  upstream_unavailable: "MCP Server 在线，但主 RAG API 不可用，请检查 API 容器健康状态。",
  empty_scope: "该 Key 没有知识库权限，请编辑 Key 并至少选择一个知识库。",
  timeout: "检查反向代理、网络连通性和服务负载后重试。",
  unexpected_response: "服务返回了无法识别的响应，请携带 Request ID 检查服务日志。",
};

export function mcpDiagnosticGuidance(code: string, serverSuggestion?: string) {
  return GUIDANCE[code] ?? serverSuggestion ?? "请携带 Request ID 检查 MCP Server 和主 API 日志。";
}
