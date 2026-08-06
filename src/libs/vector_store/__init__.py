# VectorStore abstract interface and factory

from src.libs.vector_store.base_vector_store import (
    BaseVectorStore,
    VectorRecord,
    QueryResult,
    VectorStoreError,
)
from src.libs.vector_store.collection_router import MultiCollectionVectorStore
from src.libs.vector_store.vector_store_factory import VectorStoreFactory

__all__ = [
    "BaseVectorStore",
    "MultiCollectionVectorStore",
    "VectorRecord",
    "QueryResult",
    "VectorStoreError",
    "VectorStoreFactory",
]
