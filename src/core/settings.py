"""
Configuration loading and validation module.

Loads settings from YAML file with environment variable substitution,
validates required fields, and provides a unified Settings object.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Sub-models for each configuration section
# ---------------------------------------------------------------------------

class LLMSettings(BaseModel):
    """LLM provider configuration."""
    provider: str = "openai"
    model: str = "gpt-4o"
    temperature: float = 0.0
    max_tokens: int = 4096
    api_key: str | None = None
    base_url: str | None = None


class EmbeddingSettings(BaseModel):
    """Embedding model configuration."""
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    dimensions: int = 512
    api_key: str | None = None
    base_url: str | None = None
    # Local inference settings (sentence_transformers, huggingface)
    device: str = "cpu"  # cpu | cuda | auto


class VectorStoreSettings(BaseModel):
    """Vector store backend configuration."""
    backend: str = "chroma"
    persist_path: str = "./data/db/chroma"
    collection_name: str = "default"


class SparseSettings(BaseModel):
    """Sparse / BM25 encoder configuration.

    Tokenization is fixed at 1+2-gram char n-gram for CJK; the
    field is reserved here in case a future dialect (e.g. mixed
    n-gram size) needs a runtime knob, but is not exposed in
    config/settings.yaml.
    """
    stopwords: list[str] | None = None
    min_term_len: int = 1
    drop_pure_digits: bool = True


class RetrievalSettings(BaseModel):
    """Retrieval pipeline configuration."""
    sparse_backend: str = "bm25"
    fusion_algorithm: str = "rrf"
    top_k_dense: int = 20
    top_k_sparse: int = 20
    top_k_final: int = 10
    rrf_k: int = 60


class RerankSettings(BaseModel):
    """Reranker configuration."""
    backend: str = "none"
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    top_m: int = 30
    device: str = "cpu"  # cpu | cuda | auto


class SplitterSettings(BaseModel):
    """Text splitter configuration."""
    type: str = "recursive"
    chunk_size: int = 1024
    chunk_overlap: int = 200
    separators: list[str] = Field(default_factory=lambda: ["\n\n", "\n", " ", ""])


class EvaluationSettings(BaseModel):
    """Evaluation framework configuration."""
    backends: list[str] = Field(default_factory=list)
    golden_test_set: str = "./tests/fixtures/golden_test_set.json"


class ChunkRefinerSettings(BaseModel):
    """Configuration for the ChunkRefiner transform (C5)."""
    use_llm: bool = False
    prompt_path: str = "./config/prompts/chunk_refinement.txt"
    fallback_on_error: bool = True
    # When LLM output equals the input (or differs by < this fraction of
    # length), the refiner treats the LLM as a no-op and keeps the rule
    # result instead.
    min_change_ratio: float = 0.0


class MetadataEnricherSettings(BaseModel):
    """Configuration for the MetadataEnricher transform (C6)."""
    use_llm: bool = False
    prompt_path: str = "./config/prompts/metadata_enrichment.txt"
    fallback_on_error: bool = True
    max_title_len: int = 80
    max_summary_len: int = 200
    max_tags: int = 5


class ImageCaptionerSettings(BaseModel):
    """Configuration for the ImageCaptioner transform (C7)."""
    use_llm: bool = False
    prompt_path: str = "./config/prompts/image_captioning.txt"
    fallback_on_error: bool = True
    # Max characters of an LLM-generated caption to keep. Anything
    # longer is truncated (with an ellipsis) before being written
    # to the chunk's metadata.
    max_caption_len: int = 500


class ImageClassifierHardFilterSettings(BaseModel):
    """Configuration for hard (rule-based) image classification."""
    enabled: bool = True
    size_enabled: bool = True
    min_width: float = 40.0
    min_height: float = 40.0
    min_area_ratio: float = 0.005
    # Position filtering is off by default: it is the rule most likely
    # to misclassify large body diagrams that sit near a page margin.
    position_enabled: bool = False
    header_ratio: float = 0.08
    footer_ratio: float = 0.08
    duplicate_enabled: bool = True


class ImageClassifierLLMSettings(BaseModel):
    """Configuration for LLM-based image classification."""
    enabled: bool = False
    prompt_path: str = "./config/prompts/image_classification.txt"
    fallback_on_error: bool = True


class ImageClassifierSettings(BaseModel):
    """Configuration for the ImageContentClassifier transform."""
    enabled: bool = False
    hard_filter: ImageClassifierHardFilterSettings = Field(
        default_factory=ImageClassifierHardFilterSettings
    )
    llm: ImageClassifierLLMSettings = Field(
        default_factory=ImageClassifierLLMSettings
    )


class IngestionSettings(BaseModel):
    """Top-level ingestion pipeline configuration."""
    chunk_refiner: ChunkRefinerSettings = Field(
        default_factory=ChunkRefinerSettings
    )
    metadata_enricher: MetadataEnricherSettings = Field(
        default_factory=MetadataEnricherSettings
    )
    image_captioner: ImageCaptionerSettings = Field(
        default_factory=ImageCaptionerSettings
    )
    image_classifier: ImageClassifierSettings = Field(
        default_factory=ImageClassifierSettings
    )


class ObservabilitySettings(BaseModel):
    """Observability and tracing configuration."""
    enabled: bool = True
    logging: dict[str, Any] = Field(default_factory=lambda: {
        "log_file": "./logs/traces.jsonl",
        "log_level": "INFO",
    })
    detail_level: str = "standard"


class DashboardSettings(BaseModel):
    """Dashboard configuration."""
    enabled: bool = True
    port: int = 8501
    traces_dir: str = "./logs"
    auto_refresh: bool = True
    refresh_interval: int = 5


# ---------------------------------------------------------------------------
# Root Settings model
# ---------------------------------------------------------------------------

class Settings(BaseModel):
    """
    Root configuration model for the entire application.

    Loaded from config/settings.yaml with environment variable substitution.
    """

    llm: LLMSettings = Field(default_factory=LLMSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    vector_store: VectorStoreSettings = Field(default_factory=VectorStoreSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    sparse: SparseSettings = Field(default_factory=SparseSettings)
    rerank: RerankSettings = Field(default_factory=RerankSettings)
    splitter: SplitterSettings = Field(default_factory=SplitterSettings)
    evaluation: EvaluationSettings = Field(default_factory=EvaluationSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)


# ---------------------------------------------------------------------------
# Environment variable substitution
# ---------------------------------------------------------------------------

_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _substitute_env_vars(value: Any) -> Any:
    """
    Recursively substitute ${VAR_NAME} patterns with environment variable values.

    Args:
        value: The value to process (str, dict, list, or other).

    Returns:
        The value with environment variables substituted.
    """
    if isinstance(value, str):
        def replacer(match: re.Match) -> str:
            var_name = match.group(1)
            env_value = os.environ.get(var_name, "")
            return env_value

        return _ENV_VAR_PATTERN.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    else:
        return value


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    """
    Load configuration from a YAML file.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        yaml.YAMLError: If the YAML is malformed.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    if raw_config is None:
        return {}

    # Substitute environment variables
    return _substitute_env_vars(raw_config)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_settings(config_path: str | Path | None = None) -> Settings:
    """
    Load and validate application settings.

    Args:
        config_path: Path to the YAML configuration file.
                     If None, uses default path: config/settings.yaml

    Returns:
        Validated Settings instance.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        pydantic.ValidationError: If validation fails.
    """
    if config_path is None:
        # Default to project root / config/settings.yaml
        project_root = Path(__file__).parent.parent.parent
        config_path = project_root / "config" / "settings.yaml"
    else:
        config_path = Path(config_path)

    raw_config = _load_yaml_config(config_path)
    return Settings(**raw_config)


def get_settings() -> Settings:
    """
    Get settings with caching (singleton pattern).

    Returns:
        Cached Settings instance.
    """
    if not hasattr(get_settings, "_cache"):
        get_settings._cache = load_settings()
    return get_settings._cache


def clear_settings_cache() -> None:
    """Clear the cached settings (useful for testing)."""
    if hasattr(get_settings, "_cache"):
        del get_settings._cache
