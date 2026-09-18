"""Exact-or-null cost telemetry for optional enrichment calls."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4
import time


class EnrichmentMetricsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS enrichment_runs (
                    run_id TEXT PRIMARY KEY,
                    occurred_at REAL NOT NULL,
                    operation TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    input_chars INTEGER NOT NULL,
                    output_chars INTEGER,
                    token_count INTEGER,
                    cost_usd REAL,
                    success INTEGER NOT NULL,
                    error_type TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_enrichment_runs_time
                    ON enrichment_runs(occurred_at);
            """)

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(
        self, *, operation: str, model: str, prompt_version: str,
        latency_ms: float, input_chars: int, output_chars: int | None,
        success: bool, token_count: int | None = None,
        cost_usd: float | None = None, error_type: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO enrichment_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"enrich_{uuid4().hex}", time.time(), operation, model,
                    prompt_version, latency_ms, input_chars, output_chars,
                    token_count, cost_usd, int(success), error_type,
                ),
            )

    def summary(self, since: float = 0) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT COUNT(*) AS calls,
                       SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS successes,
                       AVG(latency_ms) AS avg_latency_ms,
                       SUM(input_chars) AS input_chars,
                       SUM(output_chars) AS output_chars,
                       CASE WHEN COUNT(token_count)=COUNT(*) THEN SUM(token_count) END AS token_count,
                       CASE WHEN COUNT(cost_usd)=COUNT(*) THEN SUM(cost_usd) END AS cost_usd
                FROM enrichment_runs WHERE occurred_at >= ?
            """, (since,)).fetchone()
        return dict(row)


__all__ = ["EnrichmentMetricsStore"]
