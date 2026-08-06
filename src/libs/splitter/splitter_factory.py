"""
Splitter Factory - Creates Splitter instances based on configuration.

Supports multiple strategies: Recursive, Semantic, Fixed-length.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.libs.splitter.base_splitter import BaseSplitter, SplitterError

if TYPE_CHECKING:
    from src.core.settings import SplitterSettings


# Provider registry - maps splitter types to their module paths
_PROVIDER_REGISTRY: dict[str, str] = {
    "recursive": "src.libs.splitter.recursive_splitter.RecursiveSplitter",
    "semantic": "src.libs.splitter.semantic_splitter.SemanticSplitter",
    "fixed_length": "src.libs.splitter.fixed_length_splitter.FixedLengthSplitter",
}


def _import_provider(provider_path: str) -> type[BaseSplitter]:
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


class SplitterFactory:
    """
    Factory for creating Splitter instances based on configuration.

    Usage:
        settings = load_settings()
        splitter = SplitterFactory.create(settings.splitter)
        chunks = splitter.split_text(long_text)
    """

    @staticmethod
    def create(settings: SplitterSettings) -> BaseSplitter:
        """
        Create a Splitter instance based on type settings.

        Args:
            settings: Splitter configuration settings.

        Returns:
            BaseSplitter: An instance of the configured Splitter.

        Raises:
            SplitterError: If the type is not supported or cannot be instantiated.
        """
        splitter_type = settings.type.lower()

        if splitter_type not in _PROVIDER_REGISTRY:
            supported = ", ".join(_PROVIDER_REGISTRY.keys())
            raise SplitterError(
                f"Unsupported Splitter type: '{splitter_type}'. "
                f"Supported types: {supported}"
            )

        try:
            provider_class = _import_provider(_PROVIDER_REGISTRY[splitter_type])
            return provider_class(settings)
        except ImportError as e:
            raise SplitterError(
                f"Failed to import Splitter type '{splitter_type}': {e}. "
                f"Make sure the required dependencies are installed."
            ) from e
        except Exception as e:
            raise SplitterError(
                f"Failed to create Splitter instance for type '{splitter_type}': {e}"
            ) from e

    @staticmethod
    def register_provider(name: str, class_path: str) -> None:
        """
        Register a custom Splitter provider.

        Args:
            name: Splitter type name (e.g., 'custom').
            class_path: Dotted path to the provider class.
        """
        _PROVIDER_REGISTRY[name.lower()] = class_path

    @staticmethod
    def list_providers() -> list[str]:
        """
        List all supported Splitter types.

        Returns:
            List of splitter type names.
        """
        return list(_PROVIDER_REGISTRY.keys())
