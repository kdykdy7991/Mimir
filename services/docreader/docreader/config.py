"""SKDY DocReader service configuration.

Phase 0 placeholder. Phase 1 adapts the upstream ``docreader/config.py``
surface (gRPC port / max file size, parser concurrency, timeouts) to this
project's defaults, while keeping the local-only, no-cloud constraints from
the plan. Validation that invalid config fails at startup is deferred to the
Phase-1 config module."""