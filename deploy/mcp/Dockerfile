# Standalone MCP runtime — Phase 4 §P4.4 isolation.
#
# This image deliberately does NOT install the vector store, embedding,
# reranker, or document-parser dependencies, and does NOT mount the RAG
# data directory. It talks only to the main service's internal read-only
# HTTP API (rag_client_backend=http).
#
# Build:  docker build -f deploy/mcp/Dockerfile -t skdy-mcp:local .
# Run:    docker run --rm --network host skdy-mcp:local

FROM python:3.12-slim

WORKDIR /srv/mcp

# Minimal runtime deps only (mcp + settings/HTTP). No heavy RAG stack.
COPY deploy/mcp/requirements-mcp.txt ./requirements.txt
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
# The standalone runtime's config always points at the main API over HTTP.
COPY deploy/mcp/mcp-settings.yaml ./config/settings.yaml
RUN pip install --no-build-isolation --no-deps -e .

COPY deploy/mcp/health_check.py ./health_check.py
COPY deploy/mcp/health_check.sh ./health_check.sh
RUN chmod +x ./health_check.sh

EXPOSE 8765

# Health gate (used by compose depends_on / k8s probe): confirms the main
# API is reachable AND the read-only client can list collections. Exits 0
# only when the upstream is healthy; exits non-zero when unreachable.
HEALTHCHECK --interval=30s --timeout=15s --start-period=20s --retries=3 \
  CMD [ "./health_check.sh" ]

# Expose this MCP server over streamable-http for the standalone deployment.
CMD ["python", "-m", "src.mcp_server.server", \
     "--transport", "streamable-http", \
     "--host", "0.0.0.0", "--port", "8765", \
     "--config", "/srv/mcp/config/settings.yaml"]