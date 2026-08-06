"""
Embedding Factory - Creates Embedding instances based on configuration.

Supports multiple providers: OpenAI, Azure, Ollama.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.libs.embedding.base_embedding import BaseEmbedding, EmbeddingError

if TYPE_CHECKING:
    from src.core.settings import EmbeddingSettings


# Provider registry - maps provider names to their module paths
_PROVIDER_REGISTRY: dict[str, str] = {
    "openai": "src.libs.embedding.openai_embedding.OpenAIEmbedding",
    "sentence_transformers": "src.libs.embedding.sentence_transformers_embedding.SentenceTransformersEmbedding",
    "huggingface": "src.libs.embedding.huggingface_embedding.HuggingFaceEmbedding",
}


def _import_provider(provider_path: str) -> type[BaseEmbedding]:
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


class EmbeddingFactory:
    """
    Factory for creating Embedding instances based on configuration.

    Usage:
        settings = load_settings()
        embedding = EmbeddingFactory.create(settings.embedding)
        vectors = embedding.embed(["hello", "world"])
    """

    @staticmethod
    def create(settings: EmbeddingSettings) -> BaseEmbedding:
        """
        Create an Embedding instance based on provider settings.

        Args:
            settings: Embedding configuration settings.

        Returns:
            BaseEmbedding: An instance of the configured Embedding provider.

        Raises:
            EmbeddingError: If the provider is not supported or cannot be instantiated.
        """
        provider = settings.provider.lower()

        if provider not in _PROVIDER_REGISTRY:
            supported = ", ".join(_PROVIDER_REGISTRY.keys())
            raise EmbeddingError(
                f"Unsupported Embedding provider: '{provider}'. "
                f"Supported providers: {supported}"
            )

        try:
            provider_class = _import_provider(_PROVIDER_REGISTRY[provider])
            return provider_class(settings)
        except ImportError as e:
            raise EmbeddingError(
                f"Failed to import Embedding provider '{provider}': {e}. "
                f"Make sure the required dependencies are installed."
            ) from e
        except Exception as e:
            raise EmbeddingError(
                f"Failed to create Embedding instance for provider '{provider}': {e}"
            ) from e

    @staticmethod
    def register_provider(name: str, class_path: str) -> None:
        """
        Register a custom Embedding provider.

        Args:
            name: Provider name (e.g., 'custom').
            class_path: Dotted path to the provider class.
        """
        _PROVIDER_REGISTRY[name.lower()] = class_path

    @staticmethod
    def list_providers() -> list[str]:
        """
        List all supported Embedding providers.

        Returns:
            List of provider names.
        """
        return list(_PROVIDER_REGISTRY.keys())
