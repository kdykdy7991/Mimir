"""
LLM Factory - Creates LLM instances based on configuration.

Supports multiple providers via OpenAI-compatible API:
- OpenAI
- DeepSeek
- MiniMax
- Mimo
- vLLM
- Ollama (with OpenAI compatibility mode)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.libs.llm.base_llm import BaseLLM, LLMError

if TYPE_CHECKING:
    from src.core.settings import LLMSettings


# Provider registry - maps provider names to their module paths
# All providers use OpenAI-compatible API
_PROVIDER_REGISTRY: dict[str, str] = {
    "openai": "src.libs.llm.providers.openai_compatible.OpenAICompatibleLLM",
    "deepseek": "src.libs.llm.providers.openai_compatible.OpenAICompatibleLLM",
    "minimax": "src.libs.llm.providers.openai_compatible.OpenAICompatibleLLM",
    "mimo": "src.libs.llm.providers.openai_compatible.OpenAICompatibleLLM",
    "vllm": "src.libs.llm.providers.openai_compatible.OpenAICompatibleLLM",
    "ollama": "src.libs.llm.providers.openai_compatible.OpenAICompatibleLLM",
}


def _import_provider(provider_path: str) -> type[BaseLLM]:
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


class LLMFactory:
    """
    Factory for creating LLM instances based on configuration.

    Usage:
        settings = load_settings()
        llm = LLMFactory.create(settings.llm)
        response = llm.chat([{"role": "user", "content": "Hello"}])
    """

    @staticmethod
    def create(settings: LLMSettings) -> BaseLLM:
        """
        Create an LLM instance based on provider settings.

        Args:
            settings: LLM configuration settings.

        Returns:
            BaseLLM: An instance of the configured LLM provider.

        Raises:
            LLMError: If the provider is not supported or cannot be instantiated.
        """
        provider = settings.provider.lower()

        if provider not in _PROVIDER_REGISTRY:
            supported = ", ".join(_PROVIDER_REGISTRY.keys())
            raise LLMError(
                f"Unsupported LLM provider: '{provider}'. "
                f"Supported providers: {supported}"
            )

        try:
            provider_class = _import_provider(_PROVIDER_REGISTRY[provider])
            return provider_class(settings)
        except ImportError as e:
            raise LLMError(
                f"Failed to import LLM provider '{provider}': {e}. "
                f"Make sure the required dependencies are installed."
            ) from e
        except Exception as e:
            raise LLMError(
                f"Failed to create LLM instance for provider '{provider}': {e}"
            ) from e

    @staticmethod
    def register_provider(name: str, class_path: str) -> None:
        """
        Register a custom LLM provider.

        Args:
            name: Provider name (e.g., 'custom').
            class_path: Dotted path to the provider class.
        """
        _PROVIDER_REGISTRY[name.lower()] = class_path

    @staticmethod
    def list_providers() -> list[str]:
        """
        List all supported LLM providers.

        Returns:
            List of provider names.
        """
        return list(_PROVIDER_REGISTRY.keys())
