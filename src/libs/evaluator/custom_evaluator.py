"""
Custom Evaluator - Lightweight evaluation metrics.

Implements basic retrieval metrics: Hit Rate, MRR, Precision, Recall.
"""

from __future__ import annotations

from typing import Any

from src.libs.evaluator.base_evaluator import (
    BaseEvaluator,
    EvalReport,
    EvalResult,
)


class CustomEvaluator(BaseEvaluator):
    """
    Custom evaluator with lightweight metrics.

    Calculates:
    - hit_rate@k: Whether any relevant doc is in top-k
    - mrr: Mean Reciprocal Rank
    - precision@k: Fraction of retrieved docs that are relevant
    - recall@k: Fraction of relevant docs that are retrieved
    """

    def evaluate(
        self,
        query: str,
        retrieved_ids: list[str],
        golden_ids: list[str],
        **kwargs: Any,
    ) -> EvalReport:
        """
        Evaluate retrieval quality using custom metrics.

        Args:
            query: The original query string.
            retrieved_ids: List of document IDs returned by retrieval.
            golden_ids: List of correct document IDs (ground truth).
            **kwargs: Additional parameters (e.g., k for top-k).

        Returns:
            EvalReport: Evaluation report with metrics.
        """
        golden_set = set(golden_ids)
        metrics = []

        # Hit Rate@k
        hit = self._hit_rate(retrieved_ids, golden_set)
        metrics.append(EvalResult(metric_name="hit_rate", value=hit))

        # MRR (Mean Reciprocal Rank)
        mrr = self._mrr(retrieved_ids, golden_set)
        metrics.append(EvalResult(metric_name="mrr", value=mrr))

        # Precision@k
        precision = self._precision(retrieved_ids, golden_set)
        metrics.append(EvalResult(metric_name="precision", value=precision))

        # Recall@k
        recall = self._recall(retrieved_ids, golden_set)
        metrics.append(EvalResult(metric_name="recall", value=recall))

        return EvalReport(
            query=query,
            retrieved_ids=retrieved_ids,
            golden_ids=golden_ids,
            metrics=metrics,
        )

    def _hit_rate(self, retrieved: list[str], golden: set[str]) -> float:
        """
        Calculate Hit Rate@k.

        Returns 1.0 if any retrieved doc is relevant, 0.0 otherwise.
        """
        for doc_id in retrieved:
            if doc_id in golden:
                return 1.0
        return 0.0

    def _mrr(self, retrieved: list[str], golden: set[str]) -> float:
        """
        Calculate Mean Reciprocal Rank.

        Returns 1/rank of the first relevant doc.
        """
        for i, doc_id in enumerate(retrieved):
            if doc_id in golden:
                return 1.0 / (i + 1)
        return 0.0

    def _precision(self, retrieved: list[str], golden: set[str]) -> float:
        """
        Calculate Precision@k.

        Returns fraction of retrieved docs that are relevant.
        """
        if not retrieved:
            return 0.0
        relevant_count = sum(1 for doc_id in retrieved if doc_id in golden)
        return relevant_count / len(retrieved)

    def _recall(self, retrieved: list[str], golden: set[str]) -> float:
        """
        Calculate Recall@k.

        Returns fraction of relevant docs that are retrieved.
        """
        if not golden:
            return 0.0
        relevant_count = sum(1 for doc_id in retrieved if doc_id in golden)
        return relevant_count / len(golden)
