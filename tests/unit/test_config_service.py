"""
Unit tests for the dashboard's ``ConfigService`` (G1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.settings import Settings
from src.observability.dashboard.services.config_service import (
    ComponentCard,
    ConfigService,
)


class TestConfigService:
    def test_from_settings_uses_provided_settings(self):
        settings = Settings()
        svc = ConfigService(settings)
        assert svc.settings is settings

    def test_from_settings_path_falls_back_to_defaults_for_missing_file(
        self, tmp_path: Path,
    ):
        # No file at this path — service should still build a
        # ConfigService from default Settings, not raise.
        svc = ConfigService.from_settings_path(tmp_path / "nope.yaml")
        assert isinstance(svc.settings, Settings)

    def test_from_settings_path_loads_real_file(self, tmp_path: Path):
        config_path = tmp_path / "settings.yaml"
        config_path.write_text(
            "llm:\n  provider: openai\n  model: gpt-4o\n"
            "embedding:\n  provider: sentence_transformers\n"
            "  model: BAAI/bge-small-zh-v1.5\n"
            "  dimensions: 512\n"
            "vector_store:\n  backend: chroma\n"
            "  persist_path: ./data/db/chroma\n"
            "  collection_name: default\n",
            encoding="utf-8",
        )
        svc = ConfigService.from_settings_path(config_path)
        assert svc.settings.llm.model == "gpt-4o"
        assert svc.settings.embedding.provider == "sentence_transformers"

    def test_cards_returns_one_per_component(self):
        svc = ConfigService(Settings())
        cards = svc.cards()
        names = {c.name for c in cards}
        assert {
            "LLM", "Embedding", "Vector Store",
            "Retrieval", "Rerank", "Splitter",
        } <= names

    def test_cards_are_all_component_card(self):
        svc = ConfigService(Settings())
        for card in svc.cards():
            assert isinstance(card, ComponentCard)

    def test_rerank_card_unavailable_when_backend_none(self):
        settings = Settings()
        settings.rerank.backend = "none"
        svc = ConfigService(settings)
        rerank = next(c for c in svc.cards() if c.name == "Rerank")
        assert rerank.available is False
        assert "disabled" in rerank.summary.lower()

    def test_rerank_card_available_with_real_backend(self):
        settings = Settings()
        settings.rerank.backend = "cross_encoder"
        svc = ConfigService(settings)
        rerank = next(c for c in svc.cards() if c.name == "Rerank")
        assert rerank.available is True
        assert "cross_encoder" in rerank.summary

    def test_extra_field_carries_sub_settings(self):
        settings = Settings()
        settings.embedding.dimensions = 1024
        svc = ConfigService(settings)
        embed = next(c for c in svc.cards() if c.name == "Embedding")
        assert embed.extra["dimensions"] == 1024

    def test_to_dict_round_trip(self):
        svc = ConfigService(Settings())
        for card in svc.cards():
            d = card.to_dict()
            assert d["name"] == card.name
            assert d["provider"] == card.provider
            assert d["available"] == card.available
            assert d["extra"] == card.extra
