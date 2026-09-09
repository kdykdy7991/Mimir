# --- Build stage(s) ----------------------------------------------------------
# Phase 0 skeleton: core gRPC deps only. Format-converter deps (LibreOffice,
# openpyxl, python-docx, opendataloader-pdf, etc.) are added per Phase so the
# image stays lean until each parser actually needs it.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv/docreader

# Non-root user (plan §11): parse under nobody; root FS mounted read-only in prod.
RUN groupadd -r docreader && useradd -r -g docreader -d /srv/docreader docreader

# Keep both copy steps. The deps install stage uses build + run deps separately
# so the final layer carries only runtime setuptools-free wheels.
COPY --chown=docreader:docreader pyproject.toml README.md THIRD_PARTY_NOTICES.md ./
COPY --chown=docreader:docreader docreader ./docreader
COPY --chown=docreader:docreader tests ./tests

RUN pip install --no-cache-dir .

USER docreader
EXPOSE 50051
# Entrypoint is provided once docreader.main implements the server (Phase 1);
# leaving no CMD keeps this skeleton from pretending to serve yet.
ENTRYPOINT []