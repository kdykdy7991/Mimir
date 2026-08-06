"""
Evaluator abstract base class.

Defines the unified interface for all evaluation frameworks
(Custom metrics, Ragas, DeepEval).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalResult:
    """Single evaluation result."""
    metric_name: str
    value: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalReport:
    """Complete evaluation report for a query."""
    query: str
    retrieved_ids: list[str]
    golden_ids: list[str]
    metrics: list[EvalResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary."""
        return {
            "query": self.query,
            "retrieved_ids": self.retrieved_ids,
            "golden_ids": self.golden_ids,
            "metrics": {m.metric_name: m.value for m in self.metrics},
        }


class BaseEvaluator(ABC):
    """
    Abstract base class for evaluators.

    All Evaluator implementations must inherit from this class
    and implement the `evaluate` method.
    """

    @abstractmethod
    def evaluate(
        self,
        query: str,
        retrieved_ids: list[str],
        golden_ids: list[str],
        **kwargs: Any,
    ) -> EvalReport:
        """
        Evaluate retrieval quality for a single query.

        Args:
            query: The original query string.
            retrieved_ids: List of document IDs returned by retrieval.
            golden_ids: List of correct document IDs (ground truth).
            **kwargs: Additional parameters.

        Returns:
            EvalReport: Evaluation report with metrics.

        Raises:
            EvaluatorError: If evaluation fails.
        """
        pass

    def evaluate_batch(
        self,
        cases: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[EvalReport]:
        """
        Evaluate multiple query-retrieval cases.

        Args:
            cases: List of dicts with 'query', 'retrieved_ids', 'golden_ids'.
            **kwargs: Additional parameters.

        Returns:
            list[EvalReport]: List of evaluation reports.
        """
        results = []
        for case in cases:
            report = self.evaluate(
                query=case["query"],
                retrieved_ids=case["retrieved_ids"],
                golden_ids=case["golden_ids"],
                **kwargs,
            )
            results.append(report)
        return results


class EvaluatorError(Exception):
    """Base exception for Evaluator-related errors."""
    pass
