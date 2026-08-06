"""
SKDY RAG Server — application entry point.

Two transports are supported, selected by ``--transport``:

- ``stdio`` (default): ``python main.py`` or the ``skdy-rag``
  console script starts the MCP server on stdio — the path
  every MCP client (Copilot, Claude Desktop, Cursor) launches
  as a subprocess.
- ``streamable-http``: ``python main.py --transport streamable-http``
  starts an HTTP server (default ``127.0.0.1:8765/mcp``) for
  remote / containerised clients.

A ``--check`` flag is provided for quick config validation without
opening any sockets — useful in deployment scripts and CI.

特性:
- 全链路可插拔架构 (LLM / Embedding / VectorStore / Splitter / Reranker)
- 混合检索 (Dense Embedding + Sparse BM25 + RRF Fusion + Rerank)
- 双 MCP Transport: stdio (默认) + streamable-http (远程客户端)
- 多模态图像处理 (Image-to-Text Captioning)
- 全链路可观测 (Trace + Streamlit Dashboard)
- 自动化评估 (Ragas / Custom Metrics)

启动 MCP Server:
    python main.py                                          # stdio (默认)
    python main.py --transport streamable-http              # HTTP
    python main.py --transport streamable-http --host 0.0.0.0 --port 9000
    skdy-rag                                                  # console script

数据摄取:
    python scripts/ingest.py --path ./data/documents --collection default

查询测试:
    python scripts/query.py --query "你的问题" --top-k 5

启动 Dashboard:
    python scripts/start_dashboard.py
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from src.core.settings import load_settings


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="skdy-rag-server",
        description=(
            "MCP server for the SKDY RAG stack. Speaks MCP/JSON-RPC "
            "over stdio or streamable-http. Logs go to stderr."
        ),
    )
    parser.add_argument(
        "--config", default="./config/settings.yaml",
        help="Path to settings.yaml (default: ./config/settings.yaml).",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="Python logging level (default: INFO).",
    )
    parser.add_argument(
        "--check", action="store_true",
        help=(
            "Validate settings + print a summary, then exit. "
            "Does not start the MCP server. Useful for CI / "
            "deployment smoke tests."
        ),
    )
    parser.add_argument(
        "--transport", default="stdio",
        choices=("stdio", "streamable-http"),
        help=(
            "MCP transport: 'stdio' (default) or 'streamable-http'. "
            "stdio is what every desktop MCP client launches; "
            "streamable-http serves HTTP for remote clients."
        ),
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Bind host for --transport streamable-http "
             "(default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Bind port for --transport streamable-http "
             "(default: 8765).",
    )
    parser.add_argument(
        "--mcp-path", default="/mcp",
        help="URL path for the MCP endpoint (default: /mcp).",
    )
    return parser.parse_args(argv)


def _check_only(config_path: str) -> int:
    """Validate configuration and print a summary. Exit 0 on success."""
    try:
        settings = load_settings(config_path)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 配置加载失败: {exc}", file=sys.stderr)
        return 1
    print("✅ 配置加载成功")
    print(f"   LLM Provider:        {settings.llm.provider}")
    print(f"   Embedding Provider:  {settings.embedding.provider}")
    print(f"   Vector Store:        {settings.vector_store.backend}")
    print(f"   Sparse Backend:      {settings.retrieval.sparse_backend}")
    print(f"   Rerank Backend:      {settings.rerank.backend}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.check:
        return _check_only(args.config)

    # Defer importing the server module until here so ``--help`` and
    # ``--check`` work even if the mcp library is missing in some env.
    from src.mcp_server.server import main as server_main
    # Forward every flag we accept so the same options work via
    # ``python main.py --transport streamable-http`` and via the
    # ``skdy-rag`` console script alike.
    forwarded = [
        "--config", args.config,
        "--log-level", args.log_level,
        "--transport", args.transport,
    ]
    if args.transport == "streamable-http":
        forwarded += ["--host", args.host, "--port", str(args.port),
                      "--mcp-path", args.mcp_path]
    return server_main(forwarded)


if __name__ == "__main__":
    sys.exit(main())
