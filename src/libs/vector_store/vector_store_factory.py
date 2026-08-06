"""
VectorStore Factory - Creates VectorStore instances based on configuration.

Supports multiple backends: Chroma, Qdrant, Pinecone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.libs.vector_store.base_vector_store import BaseVectorStore, VectorStoreError

if TYPE_CHECKING:
    from src.core.settings import VectorStoreSettings


# Provider registry - maps backend names to their module paths
_PROVIDER_REGISTRY: dict[str, str] = {
    "chroma": "src.libs.vector_store.chroma_store.ChromaStore",
    "qdrant": "src.libs.vector_store.qdrant_store.QdrantStore",
    "pinecone": "src.libs.vector_store.pinecone_store.PineconeStore",
}


def _import_provider(provider_path: str) -> type[BaseVectorStore]:
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


class VectorStoreFactory:
    """
    Factory for creating VectorStore instances based on configuration.

    Usage:
        settings = load_settings()
        store = VectorStoreFactory.create(settings.vector_store)
        store.upsert(records)
        results = store.query(vector, top_k=10)
    """

    @staticmethod
    def create(settings: VectorStoreSettings) -> BaseVectorStore:
        """
        Create a VectorStore instance based on backend settings.

        Args:
            settings: VectorStore configuration settings.

        Returns:
            BaseVectorStore: An instance of the configured VectorStore backend.

        Raises:
            VectorStoreError: If the backend is not supported or cannot be instantiated.
        """
        backend = settings.backend.lower()

        if backend not in _PROVIDER_REGISTRY:
            supported = ", ".join(_PROVIDER_REGISTRY.keys())
            raise VectorStoreError(
                f"Unsupported VectorStore backend: '{backend}'. "
                f"Supported backends: {supported}"
            )

        try:
            provider_class = _import_provider(_PROVIDER_REGISTRY[backend])
            return provider_class(settings)
        except ImportError as e:
            raise VectorStoreError(
                f"Failed to import VectorStore backend '{backend}': {e}. "
                f"Make sure the required dependencies are installed."
            ) from e
        except Exception as e:
            raise VectorStoreError(
                f"Failed to create VectorStore instance for backend '{backend}': {e}"
            ) from e

    @staticmethod
    def register_provider(name: str, class_path: str) -> None:
        """
        Register a custom VectorStore provider.

        Args:
            name: Backend name (e.g., 'custom').
            class_path: Dotted path to the provider class.
        """
        _PROVIDER_REGISTRY[name.lower()] = class_path

    @staticmethod
    def list_providers() -> list[str]:
        """
        List all supported VectorStore backends.

        Returns:
            List of backend names.
        """
        return list(_PROVIDER_REGISTRY.keys())

    @staticmethod
    def create_multi_collection(settings: VectorStoreSettings) -> "MultiCollectionVectorStore":
        """
        Build a :class:`MultiCollectionVectorStore` router.

        Returns a single store object that dispatches every call to
        the right per-collection ``ChromaStore`` based on the
        ``collection=`` kwarg. Useful for the M3 multi-collection
        feature where the Web API needs one ``vector_store``
        collaborator that serves every collection.

        Currently only the ``chroma`` backend supports this wrapper
        (the router reuses ``ChromaStore`` internally). Other
        backends raise ``VectorStoreError``; future work can add
        a ``QdrantRouter`` / ``PineconeRouter`` with the same shape.
        """
        from src.libs.vector_store.collection_router import (
            MultiCollectionVectorStore,
        )

        backend = settings.backend.lower()
        if backend != "chroma":
            raise VectorStoreError(
                f"MultiCollectionVectorStore currently only supports the "
                f"'chroma' backend; got '{backend}'.",
            )
        return MultiCollectionVectorStore(settings)
