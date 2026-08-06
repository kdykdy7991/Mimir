"""
Unit tests for Custom Evaluator.

Tests cover:
- Hit Rate calculation
- MRR calculation
- Precision calculation
- Recall calculation
- EvaluatorFactory routing
"""

from __future__ import annotations

import pytest

from src.core.settings import EvaluationSettings
from src.libs.evaluator.base_evaluator import (
    BaseEvaluator,
    EvalReport,
    EvalResult,
    EvaluatorError,
)
from src.libs.evaluator.custom_evaluator import CustomEvaluator
from src.libs.evaluator.evaluator_factory import EvaluatorFactory


# ---------------------------------------------------------------------------
# Tests: CustomEvaluator metrics
# ---------------------------------------------------------------------------

class TestCustomEvaluatorMetrics:
    """Test CustomEvaluator metric calculations."""

    def test_perfect_retrieval(self):
        """All relevant docs retrieved at top positions."""
        evaluator = CustomEvaluator()
        report = evaluator.evaluate(
            query="test query",
            retrieved_ids=["doc1", "doc2", "doc3"],
            golden_ids=["doc1", "doc2"],
        )

        metrics = {m.metric_name: m.value for m in report.metrics}
        assert metrics["hit_rate"] == 1.0
        assert metrics["mrr"] == 1.0  # First relevant at rank 1
        assert metrics["precision"] == pytest.approx(2 / 3)  # 2 relevant out of 3
        assert metrics["recall"] == 1.0  # All relevant docs found

    def test_no_relevant_docs(self):
        """No relevant docs in retrieved results."""
        evaluator = CustomEvaluator()
        report = evaluator.evaluate(
            query="test query",
            retrieved_ids=["doc4", "doc5", "doc6"],
            golden_ids=["doc1", "doc2"],
        )

        metrics = {m.metric_name: m.value for m in report.metrics}
        assert metrics["hit_rate"] == 0.0
        assert metrics["mrr"] == 0.0
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0

    def test_partial_retrieval(self):
        """Some relevant docs retrieved."""
        evaluator = CustomEvaluator()
        report = evaluator.evaluate(
            query="test query",
            retrieved_ids=["doc4", "doc1", "doc5"],  # doc1 at rank 2
            golden_ids=["doc1", "doc2"],
        )

        metrics = {m.metric_name: m.value for m in report.metrics}
        assert metrics["hit_rate"] == 1.0  # Found at least one
        assert metrics["mrr"] == pytest.approx(0.5)  # 1/2
        assert metrics["precision"] == pytest.approx(1 / 3)  # 1 relevant out of 3
        assert metrics["recall"] == pytest.approx(0.5)  # 1 of 2 relevant found

    def test_mrr_first_at_rank_3(self):
        """MRR when first relevant doc is at rank 3."""
        evaluator = CustomEvaluator()
        report = evaluator.evaluate(
            query="test query",
            retrieved_ids=["doc4", "doc5", "doc1"],
            golden_ids=["doc1"],
        )

        metrics = {m.metric_name: m.value for m in report.metrics}
        assert metrics["mrr"] == pytest.approx(1 / 3)

    def test_empty_retrieved(self):
        """Empty retrieved list."""
        evaluator = CustomEvaluator()
        report = evaluator.evaluate(
            query="test query",
            retrieved_ids=[],
            golden_ids=["doc1"],
        )

        metrics = {m.metric_name: m.value for m in report.metrics}
        assert metrics["hit_rate"] == 0.0
        assert metrics["mrr"] == 0.0
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0

    def test_empty_golden(self):
        """Empty golden list (no ground truth)."""
        evaluator = CustomEvaluator()
        report = evaluator.evaluate(
            query="test query",
            retrieved_ids=["doc1", "doc2"],
            golden_ids=[],
        )

        metrics = {m.metric_name: m.value for m in report.metrics}
        assert metrics["hit_rate"] == 0.0
        assert metrics["mrr"] == 0.0
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0  # No golden = no recall


# ---------------------------------------------------------------------------
# Tests: EvalReport
# ---------------------------------------------------------------------------

class TestEvalReport:
    """Test EvalReport dataclass."""

    def test_to_dict(self):
        """to_dict() converts report to dictionary."""
        report = EvalReport(
            query="test",
            retrieved_ids=["doc1"],
            golden_ids=["doc2"],
            metrics=[
                EvalResult(metric_name="hit_rate", value=1.0),
                EvalResult(metric_name="mrr", value=0.5),
            ],
        )

        d = report.to_dict()
        assert d["query"] == "test"
        assert d["retrieved_ids"] == ["doc1"]
        assert d["golden_ids"] == ["doc2"]
        assert d["metrics"]["hit_rate"] == 1.0
        assert d["metrics"]["mrr"] == 0.5


# ---------------------------------------------------------------------------
# Tests: EvaluatorFactory
# ---------------------------------------------------------------------------

class TestEvaluatorFactory:
    """Test EvaluatorFactory routing logic."""

    def test_list_providers(self):
        """list_providers() returns supported backends."""
        providers = EvaluatorFactory.list_providers()
        assert isinstance(providers, list)
        assert "custom" in providers
        assert "ragas" in providers

    def test_create_custom_evaluator(self):
        """Factory creates CustomEvaluator for backend='custom'."""
        settings = EvaluationSettings(backends=["custom"])
        evaluator = EvaluatorFactory.create(settings)

        assert isinstance(evaluator, CustomEvaluator)

    def test_create_default_evaluator(self):
        """Factory creates CustomEvaluator when no backends configured."""
        settings = EvaluationSettings(backends=[])
        evaluator = EvaluatorFactory.create(settings)

        assert isinstance(evaluator, CustomEvaluator)

    def test_unsupported_backend_raises_error(self):
        """Factory raises EvaluatorError for unsupported backend."""
        settings = EvaluationSettings(backends=["nonexistent"])

        with pytest.raises(EvaluatorError) as exc_info:
            EvaluatorFactory.create(settings)

        assert "nonexistent" in str(exc_info.value)

    def test_backend_case_insensitive(self):
        """Factory handles backend names case-insensitively."""
        settings = EvaluationSettings(backends=["CUSTOM"])
        evaluator = EvaluatorFactory.create(settings)
        assert isinstance(evaluator, CustomEvaluator)

    def test_evaluate_batch(self):
        """evaluate_batch() processes multiple cases."""
        evaluator = CustomEvaluator()
        cases = [
            {"query": "q1", "retrieved_ids": ["doc1"], "golden_ids": ["doc1"]},
            {"query": "q2", "retrieved_ids": ["doc2"], "golden_ids": ["doc3"]},
        ]

        reports = evaluator.evaluate_batch(cases)
        assert len(reports) == 2
        assert reports[0].query == "q1"
        assert reports[1].query == "q2"
