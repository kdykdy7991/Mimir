"""
Unit tests for configuration loading and validation.

Tests cover:
- YAML loading with environment variable substitution
- Settings validation
- Default values
- Required field validation
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.core.settings import (
    Settings,
    load_settings,
    clear_settings_cache,
    _substitute_env_vars,
    _load_yaml_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear settings cache before each test."""
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def sample_config(tmp_path: Path) -> Path:
    """Create a minimal test configuration file."""
    config = {
        "llm": {
            "provider": "openai",
            "model": "gpt-4",
            "api_key": "${TEST_API_KEY}",
        },
        "embedding": {
            "provider": "openai",
            "model": "text-embedding-3-small",
        },
        "vector_store": {
            "backend": "chroma",
        },
    }
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


@pytest.fixture
def full_config(tmp_path: Path) -> Path:
    """Create a full test configuration file."""
    config = {
        "llm": {
            "provider": "azure",
            "model": "gpt-4o",
            "temperature": 0.1,
            "max_tokens": 2048,
            "azure_endpoint": "${AZURE_ENDPOINT}",
            "api_key": "${AZURE_KEY}",
        },
        "embedding": {
            "provider": "openai",
            "model": "text-embedding-3-large",
            "dimensions": 1024,
        },
        "vector_store": {
            "backend": "chroma",
            "persist_path": "/tmp/test_chroma",
            "collection_name": "test_collection",
        },
        "retrieval": {
            "sparse_backend": "bm25",
            "fusion_algorithm": "rrf",
            "top_k_dense": 30,
            "top_k_sparse": 30,
            "top_k_final": 15,
            "rrf_k": 60,
        },
        "rerank": {
            "backend": "cross_encoder",
            "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "top_m": 20,
        },
        "splitter": {
            "type": "recursive",
            "chunk_size": 512,
            "chunk_overlap": 100,
        },
        "evaluation": {
            "backends": ["ragas"],
        },
        "observability": {
            "enabled": True,
        },
        "dashboard": {
            "enabled": True,
            "port": 8502,
        },
    }
    config_path = tmp_path / "settings.yaml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")
    return config_path


# ---------------------------------------------------------------------------
# Tests: Environment variable substitution
# ---------------------------------------------------------------------------

class TestEnvVarSubstitution:
    """Test environment variable substitution logic."""

    def test_simple_substitution(self):
        """Test basic ${VAR} substitution."""
        os.environ["TEST_VAR"] = "hello"
        result = _substitute_env_vars("prefix_${TEST_VAR}_suffix")
        assert result == "prefix_hello_suffix"

    def test_multiple_substitutions(self):
        """Test multiple ${VAR} in one string."""
        os.environ["VAR_A"] = "aaa"
        os.environ["VAR_B"] = "bbb"
        result = _substitute_env_vars("${VAR_A}-${VAR_B}")
        assert result == "aaa-bbb"

    def test_missing_env_var(self):
        """Test missing env var results in empty string."""
        # Make sure the var doesn't exist
        os.environ.pop("NONEXISTENT_VAR_12345", None)
        result = _substitute_env_vars("${NONEXISTENT_VAR_12345}")
        assert result == ""

    def test_dict_substitution(self):
        """Test substitution in nested dict."""
        os.environ["API_KEY"] = "secret"
        data = {"key": "${API_KEY}", "nested": {"val": "${API_KEY}"}}
        result = _substitute_env_vars(data)
        assert result == {"key": "secret", "nested": {"val": "secret"}}

    def test_list_substitution(self):
        """Test substitution in list."""
        os.environ["ITEM"] = "value"
        data = ["${ITEM}", "static", "${ITEM}"]
        result = _substitute_env_vars(data)
        assert result == ["value", "static", "value"]

    def test_non_string_passthrough(self):
        """Test non-string values pass through unchanged."""
        assert _substitute_env_vars(42) == 42
        assert _substitute_env_vars(True) is True
        assert _substitute_env_vars(None) is None


# ---------------------------------------------------------------------------
# Tests: YAML loading
# ---------------------------------------------------------------------------

class TestYamlLoading:
    """Test YAML configuration file loading."""

    def test_load_existing_file(self, sample_config: Path):
        """Test loading an existing YAML file."""
        config = _load_yaml_config(sample_config)
        assert config["llm"]["provider"] == "openai"
        assert config["llm"]["model"] == "gpt-4"

    def test_load_nonexistent_file(self, tmp_path: Path):
        """Test loading a non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            _load_yaml_config(tmp_path / "nonexistent.yaml")

    def test_load_empty_file(self, tmp_path: Path):
        """Test loading an empty YAML file returns empty dict."""
        config_path = tmp_path / "empty.yaml"
        config_path.write_text("", encoding="utf-8")
        config = _load_yaml_config(config_path)
        assert config == {}

    def test_env_var_in_yaml(self, sample_config: Path):
        """Test environment variables are substituted in loaded config."""
        os.environ["TEST_API_KEY"] = "my-secret-key"
        config = _load_yaml_config(sample_config)
        assert config["llm"]["api_key"] == "my-secret-key"


# ---------------------------------------------------------------------------
# Tests: Settings model
# ---------------------------------------------------------------------------

class TestSettingsModel:
    """Test Settings Pydantic model validation."""

    def test_default_settings(self):
        """Test creating Settings with all defaults."""
        settings = Settings()
        assert settings.llm.provider == "openai"
        assert settings.llm.model == "gpt-4o"
        assert settings.embedding.provider == "openai"
        assert settings.vector_store.backend == "chroma"
        assert settings.retrieval.top_k_final == 10

    def test_custom_settings(self):
        """Test creating Settings with custom values."""
        settings = Settings(
            llm={"provider": "ollama", "model": "llama3"},
            embedding={"provider": "ollama", "model": "nomic"},
        )
        assert settings.llm.provider == "ollama"
        assert settings.llm.model == "llama3"
        assert settings.embedding.provider == "ollama"

    def test_partial_override(self):
        """Test partial override preserves defaults."""
        settings = Settings(llm={"model": "gpt-4"})
        assert settings.llm.model == "gpt-4"
        assert settings.llm.provider == "openai"  # default preserved

    def test_nested_defaults(self):
        """Test nested model defaults are applied."""
        settings = Settings()
        assert settings.rerank.backend == "none"
        assert settings.splitter.chunk_size == 1024
        assert settings.dashboard.port == 8501


# ---------------------------------------------------------------------------
# Tests: load_settings()
# ---------------------------------------------------------------------------

class TestLoadSettings:
    """Test the load_settings() public API."""

    def test_load_from_file(self, sample_config: Path):
        """Test loading settings from a YAML file."""
        settings = load_settings(sample_config)
        assert isinstance(settings, Settings)
        assert settings.llm.provider == "openai"
        assert settings.llm.model == "gpt-4"

    def test_load_with_env_vars(self, sample_config: Path):
        """Test environment variables are resolved."""
        os.environ["TEST_API_KEY"] = "resolved-key"
        settings = load_settings(sample_config)
        assert settings.llm.api_key == "resolved-key"

    def test_load_full_config(self, full_config: Path):
        """Test loading a full configuration file."""
        settings = load_settings(full_config)
        assert settings.llm.provider == "azure"
        assert settings.llm.temperature == 0.1
        assert settings.embedding.dimensions == 1024
        assert settings.vector_store.persist_path == "/tmp/test_chroma"
        assert settings.retrieval.top_k_dense == 30
        assert settings.rerank.backend == "cross_encoder"
        assert settings.splitter.chunk_size == 512
        assert settings.evaluation.backends == ["ragas"]
        assert settings.dashboard.port == 8502

    def test_load_default_path(self):
        """Test loading from default config/settings.yaml."""
        # This should work if the file exists
        settings = load_settings()
        assert isinstance(settings, Settings)

    def test_load_nonexistent_file(self, tmp_path: Path):
        """Test loading non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            load_settings(tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# Tests: Settings validation
# ---------------------------------------------------------------------------

class TestSettingsValidation:
    """Test Settings validation rules."""

    def test_invalid_provider_type(self):
        """Test that provider must be a string (Pydantic v2 strict)."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Settings(llm={"provider": 123})

    def test_negative_chunk_size(self):
        """Test negative chunk_size is allowed (Pydantic doesn't enforce range by default)."""
        settings = Settings(splitter={"chunk_size": -100})
        assert settings.splitter.chunk_size == -100

    def test_empty_list_backend(self):
        """Test empty backends list is valid."""
        settings = Settings(evaluation={"backends": []})
        assert settings.evaluation.backends == []
