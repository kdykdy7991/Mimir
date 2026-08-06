"""
Generate the smoke-test PDF.

Three pages of plain text so the ingestion pipeline produces
multiple chunks and the query step has something to retrieve.
The text is intentionally varied so chunking and retrieval
produce non-trivial results.

Writes to ``/tmp/smoke.pdf`` (the smoke runner expects this
path; change ``SMOKE_PDF`` in ``run_smoke.py`` to relocate).
"""

from __future__ import annotations

import pymupdf

PAGES = [
    "SKDY RAG Server — Smoke Test\n\n"
    "This document exercises the full ingestion → query pipeline "
    "against a small, deterministic PDF. The text below is "
    "intentionally varied so chunking and retrieval produce "
    "non-trivial results.\n\n"
    "Topics: Vector Search, BM25, Reranking, MCP, Observability.",

    "Page 2: Hybrid Retrieval\n\n"
    "The pipeline combines dense embeddings (semantic) with sparse "
    "BM25 (lexical). Reciprocal Rank Fusion merges the two ranked "
    "lists. An optional reranker (Cross-Encoder or LLM) refines "
    "the top candidates before the final response is built.\n\n"
    "Image-to-Text captioning is used at ingestion time so that "
    "the same retrieval surface can answer questions about the "
    "visual content of a document.",

    "Page 3: MCP and Observability\n\n"
    "The MCP server exposes three tools: query_knowledge_hub, "
    "list_collections, and get_document_summary. Every call is "
    "traced end-to-end via TraceContext and persisted as JSONL "
    "for offline inspection.\n\n"
    "A Streamlit dashboard provides a browser-friendly view of "
    "documents, traces, and golden-test-set evaluation results.",
]


def main() -> None:
    doc = pymupdf.open()
    for text in PAGES:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=12)
    out = "/tmp/smoke.pdf"
    doc.save(out)
    doc.close()
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
