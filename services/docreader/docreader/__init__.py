"""SKDY DocReader — standalone local document-parsing gRPC service.

This service is a controlled migration of WeKnora `docreader`
(see ``../../provenance`` and ``../../README.md``). It converts file/URL
bytes into Markdown, image bytes and parse metadata only.

Layout mirrors the upstream ``docreader`` package so future upstream syncs stay
cheap: project-specific adaptations live in the main service
(``src/document_parser/``) rather than here.

Phase 0: package skeleton only — no functional parsers yet.
"""

__version__ = "0.1.0"