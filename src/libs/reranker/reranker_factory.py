"""
Reranker Factory - Creates Reranker instances based on configuration.

Supports multiple backends: None, CrossEncoder, LLM.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.libs.reranker.base_reranker import BaseReranker, NoneReranker, RerankerError

if TYPE_CHECKING:
    from src.core.settings import RerankSettings


# Provider registry - maps backend names to their module paths
# 'none' is handled specially as it doesn't need a real implementation
_PROVIDER_REGISTRY: dict[str, str] = {
    "cross_encoder": "src.libs.reranker.cross_encoder_reranker.CrossEncoderReranker",
    "llm": "src.libs.reranker.llm_reranker.LLMReranker",
}


def _import_provider(provider_path: str) -> type[BaseReranker]:
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


class RerankerFactory:
    """
    Factory for creating Reranker instances based on configuration.

    Usage:
        settings = load_settings()
        reranker = RerankerFactory.create(settings.rerank)
        ranked = reranker.rerank(query, candidates)
    """

    @staticmethod
    def create(settings: RerankSettings) -> BaseReranker:
        """
        Create a Reranker instance based on backend settings.

        Args:
            settings: Rerank configuration settings.

        Returns:
            BaseReranker: An instance of the configured Reranker backend.

        Raises:
            RerankerError: If the backend is not supported or cannot be instantiated.
        """
        backend = settings.backend.lower()

        # Special case: 'none' returns the no-op reranker
        if backend == "none":
            return NoneReranker()

        if backend not in _PROVIDER_REGISTRY:
            supported = ["none"] + list(_PROVIDER_REGISTRY.keys())
            raise RerankerError(
                f"Unsupported Reranker backend: '{backend}'. "
                f"Supported backends: {', '.join(supported)}"
            )

        try:
            provider_class = _import_provider(_PROVIDER_REGISTRY[backend])
            return provider_class(settings)
        except ImportError as e:
            raise RerankerError(
                f"Failed to import Reranker backend '{backend}': {e}. "
                f"Make sure the required dependencies are installed."
            ) from e
        except Exception as e:
            raise RerankerError(
                f"Failed to create Reranker instance for backend '{backend}': {e}"
            ) from e

    @staticmethod
    def register_provider(name: str, class_path: str) -> None:
        """
        Register a custom Reranker provider.

        Args:
            name: Backend name (e.g., 'custom').
            class_path: Dotted path to the provider class.
        """
        _PROVIDER_REGISTRY[name.lower()] = class_path

    @staticmethod
    def list_providers() -> list[str]:
        """
        List all supported Reranker backends.

        Returns:
            List of backend names.
        """
        return ["none"] + list(_PROVIDER_REGISTRY.keys())
