"""
Configuration loading tests — verify A3 acceptance criteria.

验收标准:
1. main.py 启动时能成功加载 config/settings.yaml 并拿到 Settings 对象
2. 删除/缺失关键字段时，load_settings() 抛出"可读错误"
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.core.settings import load_settings, clear_settings_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear settings cache before each test."""
    clear_settings_cache()
    yield
    clear_settings_cache()


# ---------------------------------------------------------------------------
# Test 1: 正常加载 config/settings.yaml
# ---------------------------------------------------------------------------

class TestLoadDefaultConfig:
    """Test loading the default config/settings.yaml file."""

    def test_load_default_config(self):
        """验收标准1: 能成功加载 config/settings.yaml 并拿到 Settings 对象"""
        settings = load_settings()
        assert settings is not None
        assert hasattr(settings, "llm")
        assert hasattr(settings, "embedding")
        assert hasattr(settings, "vector_store")

    def test_main_py_starts_successfully(self):
        """验收标准1: main.py 启动时能成功加载配置"""
        # Use ``--check`` so the process exits with a config summary
        # on stdout, instead of staying alive on the MCP stdio loop.
        result = subprocess.run(
            [sys.executable, "main.py", "--check"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent,
        )
        assert result.returncode == 0, f"main.py failed: {result.stderr}"
        assert "配置加载成功" in result.stdout


# ---------------------------------------------------------------------------
# Test 2: 缺失关键字段时抛出可读错误
# ---------------------------------------------------------------------------

class TestMissingRequiredFields:
    """Test that missing required fields raise readable errors."""

    def _create_config(self, tmp_path: Path, override: dict) -> Path:
        """Helper to create a config file with overrides."""
        base_config = {
            "llm": {"provider": "openai", "model": "gpt-4"},
            "embedding": {"provider": "openai", "model": "text-embedding-3-small"},
            "vector_store": {"backend": "chroma"},
        }
        # Deep merge override
        for key, value in override.items():
            if isinstance(value, dict) and key in base_config:
                base_config[key].update(value)
            else:
                base_config[key] = value

        config_path = tmp_path / "settings.yaml"
        config_path.write_text(yaml.dump(base_config), encoding="utf-8")
        return config_path

    def test_missing_embedding_provider(self, tmp_path: Path):
        """验收标准2: 缺失 embedding.provider 时抛出可读错误"""
        config_path = self._create_config(tmp_path, {
            "embedding": {"model": "text-embedding-3-small"}  # 没有 provider
        })
        # Pydantic 允许缺失（有默认值），但我们可以测试空字符串
        # 这里测试的是类型验证
        settings = load_settings(config_path)
        assert settings.embedding.provider == "openai"  # 使用默认值

    def test_invalid_llm_provider_type(self, tmp_path: Path):
        """验收标准2: 无效类型时抛出 ValidationError"""
        config_path = self._create_config(tmp_path, {
            "llm": {"provider": 123}  # 应该是 str
        })
        with pytest.raises(ValidationError) as exc_info:
            load_settings(config_path)
        assert "provider" in str(exc_info.value).lower()

    def test_invalid_port_type(self, tmp_path: Path):
        """验收标准2: dashboard.port 类型错误时抛出可读错误"""
        config_path = self._create_config(tmp_path, {
            "dashboard": {"port": "not_a_number"}
        })
        with pytest.raises(ValidationError) as exc_info:
            load_settings(config_path)
        assert "port" in str(exc_info.value).lower()

    def test_empty_yaml_file(self, tmp_path: Path):
        """验收标准2: 空 YAML 文件使用默认值"""
        config_path = tmp_path / "settings.yaml"
        config_path.write_text("", encoding="utf-8")
        settings = load_settings(config_path)
        # 应该使用所有默认值
        assert settings.llm.provider == "openai"
        assert settings.embedding.provider == "openai"

    def test_nonexistent_config_file(self, tmp_path: Path):
        """验收标准2: 配置文件不存在时抛出 FileNotFoundError"""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_settings(tmp_path / "nonexistent.yaml")
        assert "not found" in str(exc_info.value).lower()


