from src.ingestion.storage import EnrichmentMetricsStore


def test_metrics_preserve_unknown_tokens_and_cost_as_null(tmp_path):
    store = EnrichmentMetricsStore(tmp_path / "metrics.sqlite3")
    store.record(
        operation="tag_suggestions", model="m", prompt_version="p",
        latency_ms=12.5, input_chars=100, output_chars=20, success=True,
    )
    summary = store.summary()
    assert summary["calls"] == summary["successes"] == 1
    assert summary["token_count"] is None
    assert summary["cost_usd"] is None
    assert summary["avg_latency_ms"] == 12.5


def test_metrics_sum_exact_provider_usage_only_when_complete(tmp_path):
    store = EnrichmentMetricsStore(tmp_path / "metrics.sqlite3")
    for tokens, cost in [(10, 0.01), (20, 0.02)]:
        store.record(
            operation="derived", model="m", prompt_version="p",
            latency_ms=10, input_chars=5, output_chars=2, success=True,
            token_count=tokens, cost_usd=cost,
        )
    summary = store.summary()
    assert summary["token_count"] == 30
    assert summary["cost_usd"] == 0.03
