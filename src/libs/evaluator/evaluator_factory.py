"""
Evaluator Factory - Creates Evaluator instances based on configuration.

Supports multiple backends: Custom, Ragas, DeepEval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.libs.evaluator.base_evaluator import BaseEvaluator, EvaluatorError
from src.libs.evaluator.custom_evaluator import CustomEvaluator

if TYPE_CHECKING:
    from src.core.settings import EvaluationSettings


# Provider registry - maps backend names to their module paths
# 'custom' is handled specially as it's always available
_PROVIDER_REGISTRY: dict[str, str] = {
    "ragas": "src.libs.evaluator.ragas_evaluator.RagasEvaluator",
    "deepeval": "src.libs.evaluator.deepeval_evaluator.DeepEvalEvaluator",
}


def _import_provider(provider_path: str) -> type[BaseEvaluator]:
    """
    Dynamically import a provider class.

    Args:
        provider_path: Dotted path to the provider class.

    Returns:
        The provider class.

    Raises:
        ImportError: If the module cannot be imported.
    """
    module_path, class_name = provider_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class EvaluatorFactory:
    """
    Factory for creating Evaluator instances based on configuration.

    Usage:
        settings = load_settings()
        evaluator = EvaluatorFactory.create(settings.evaluation)
        report = evaluator.evaluate(query, retrieved_ids, golden_ids)
    """

    @staticmethod
    def create(settings: EvaluationSettings) -> BaseEvaluator:
        """
        Create an Evaluator instance based on backend settings.

        Args:
            settings: Evaluation configuration settings.

        Returns:
            BaseEvaluator: An instance of the configured Evaluator.

        Raises:
            EvaluatorError: If the backend is not supported or cannot be instantiated.
        """
        # If no backends configured, use custom evaluator
        if not settings.backends:
            return CustomEvaluator()

        # Use the first configured backend
        backend = settings.backends[0].lower()

        # Special case: 'custom' is always available
        if backend == "custom":
            return CustomEvaluator()

        if backend not in _PROVIDER_REGISTRY:
            supported = ["custom"] + list(_PROVIDER_REGISTRY.keys())
            raise EvaluatorError(
                f"Unsupported Evaluator backend: '{backend}'. "
                f"Supported backends: {', '.join(supported)}"
            )

        try:
            provider_class = _import_provider(_PROVIDER_REGISTRY[backend])
            return provider_class(settings)
        except ImportError as e:
            raise EvaluatorError(
                f"Failed to import Evaluator backend '{backend}': {e}. "
                f"Make sure the required dependencies are installed."
            ) from e
        except Exception as e:
            raise EvaluatorError(
                f"Failed to create Evaluator instance for backend '{backend}': {e}"
            ) from e

    @staticmethod
    def register_provider(name: str, class_path: str) -> None:
        """
        Register a custom Evaluator provider.

        Args:
            name: Backend name (e.g., 'custom').
            class_path: Dotted path to the provider class.
        """
        _PROVIDER_REGISTRY[name.lower()] = class_path

    @staticmethod
    def list_providers() -> list[str]:
        """
        List all supported Evaluator backends.

        Returns:
            List of backend names.
        """
        return ["custom"] + list(_PROVIDER_REGISTRY.keys())
