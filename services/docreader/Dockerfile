# DocReader parsing service — standalone local gRPC document parser.
#
# Runtime deps for the builtin production formats are declared in pyproject.toml
# (gRPC/protobuf + pymupdf for PDF). This stage installs only the Python deps;
# the image stays lean (no LibreOffice/Java/OLE2 — DOC/XLS/PPT are gated
# placeholders until a converter is added on deployment).
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv/docreader

# Non-root user (plan §11): parse under nobody; root FS mounted read-only in prod.
RUN groupadd -r docreader && useradd -r -g docreader -d /srv/docreader docreader

COPY --chown=docreader:docreader pyproject.toml README.md THIRD_PARTY_NOTICES.md ./
COPY --chown=docreader:docreader docreader ./docreader
COPY --chown=docreader:docreader tests ./tests

RUN pip install --no-cache-dir .

USER docreader
EXPOSE 50051

# Health / readiness gate: waits for the channel READY + a real ListEngines +
# builtin-engine minimum-format check. Non-zero exit marks the container unhealthy
# (docker compose restarts it). Env overrides: DOCREADER_HEALTH_ENDPOINT /
# DOCREADER_HEALTH_TIMEOUT.
HEALTHCHECK --interval=30s --timeout=20s --start-period=20s --retries=3 \
    CMD ["python", "-m", "docreader.health_check"]

# Entrypoint: serve the DocReader gRPC service (docreader/main.serve). No shell
# wrapper — the container runs python directly so signals reach the process.
CMD ["python", "-m", "docreader.main"]