# THIRD-PARTY ATTRIBUTIONS

This project reuses (never absorbs) design ideas or code from the following
open-source projects. In each case the borrow is narrow, documented in the
source file header, and reported in the migration plan
(`docs/plan-2026-09-10-weknora-readonly-mcp-optimization.md`, §4).

## WeKnora — MIT License

- Project: WeKnora (open-source enterprise RAG / MCP, formerly FastGPT)
- Reference commit: `3e6010e7cd3937f289cc1dbadc829e71eb1163f4`
  (the WeKnora HEAD that this work was checked against).
- License: MIT.
- Borrowed element: the MCP `<client.py>` HTTP client design —
  a base URL + a per-thread reused HTTP session + credentials carried on a
  request header (`X-API-Key`), and the read-tool surface names/contract
  (`list_knowledge_bases` / `get_knowledge` / `list_chunks` with
  `page` / `page_size`).
- Use in this repository: `src/mcp_server/clients/http_client.py`
  (`HttpRagReadOnlyClient`), attributed in the file header; its four read
  methods intentionally implement only the read surface and deliberately
  expose **no** generic `request(method, path, ...)` escape hatch.

The referenced commit's LICENSE can be inspected at
`/home/hello/workspace/WeKnora` (external checkout used during this work).