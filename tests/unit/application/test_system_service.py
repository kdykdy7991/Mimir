"""
Unit tests for ``SystemService`` (M1 application layer).

``SystemService`` derives ``/system/info`` and ``/system/health`` from
``Settings``. Tests use real default Settings (no network, no keys) so
provider presence / model resolution is exercised end-to-end.
"""

from __future__ import annotations

import pytest

from src.application.services import (
    ProviderStatusView,
    SystemHealthView,
    SystemInfoView,
    SystemService,
)
from src.core.settings import Settings
from src.libs.embedding import EmbeddingError


@pytest.fixture
def svc() -> SystemService:
    return SystemService(Settings())


class _OkEmbedding:
    """A healthy embedding client — embed_single succeeds."""

    def embed_single(self, text: str) -> list[float]:
        return [0.1, 0.2]


class _FailEmbedding:
    """An embedding client whose probe raises (provider unreachable)."""

    def embed_single(self, text: str) -> list[float]:
        raise EmbeddingError("connection refused")


class _OkStore:
    def heartbeat(self) -> dict:
        return {"nanosecond heartbeat": 1}


class _OkDb:
    def ping(self) -> None:
        return None


class TestGetInfo:
    def test_returns_view(self, svc: SystemService) -> None:
        info = svc.get_info()
        assert isinstance(info, SystemInfoView)
        assert info.app_name == SystemService.APP_NAME
        assert info.version == SystemService.VERSION

    def test_default_backends(self, svc: SystemService) -> None:
        info = svc.get_info()
        # Default settings: chroma / bm25 / rerank none.
        assert info.storage_backend == "chroma"
        assert info.sparse_backend == "bm25"
        assert info.rerank_backend == "none"

    def test_provider_presence_from_default_settings(self, svc: SystemService) -> None:
        info = svc.get_info()
        # Default Settings ships models for llm + embedding; vision is absent.
        assert set(info.providers) == {"llm", "embedding", "vision"}
        llm = info.providers["llm"]
        assert isinstance(llm, ProviderStatusView)
        assert llm.configured is True
        assert llm.model == "gpt-4o"
        assert info.providers["embedding"].configured is True
        assert info.providers["embedding"].model == "text-embedding-3-small"
        # Vision is optional in v0.1 — absent by default.
        assert info.providers["vision"].configured is False
        assert info.providers["vision"].model is None

    def test_ready_reflects_config_and_probes(self, svc: SystemService) -> None:
        # M3: ready is real — embedding reflects the (unwired) probe,
        # llm/vision reflect configuration presence (never True when
        # unconfigured).
        info = svc.get_info()
        assert info.providers["llm"].ready is True       # configured
        assert info.providers["embedding"].ready is False  # probe degraded
        assert info.providers["vision"].ready is False     # not configured

    def test_embedding_ready_tracks_probe(self, tmp_path) -> None:
        healthy = SystemService(
            Settings(), embedding=_OkEmbedding(),
            vector_store=_OkStore(), db=_OkDb(), data_dir=str(tmp_path),
        )
        assert healthy.get_info().providers["embedding"].ready is True

        failing = SystemService(
            Settings(), embedding=_FailEmbedding(),
            vector_store=_OkStore(), db=_OkDb(), data_dir=str(tmp_path),
        )
        assert failing.get_info().providers["embedding"].ready is False


class TestGetHealth:
    def test_unwired_reports_degraded_with_known_deps(
        self, svc: SystemService,
    ) -> None:
        health = svc.get_health()
        assert isinstance(health, SystemHealthView)
        assert health.status == "degraded"
        assert {d.name for d in health.dependencies} == {
            "embedding", "chroma", "sqlite", "bm25",
        }
        assert all(d.status == "degraded" for d in health.dependencies)

    def test_all_healthy_reports_ok(self, tmp_path) -> None:
        healthy = SystemService(
            Settings(), embedding=_OkEmbedding(),
            vector_store=_OkStore(), db=_OkDb(), data_dir=str(tmp_path),
        )
        health = healthy.get_health()
        assert health.status == "ok"
        assert {d.status for d in health.dependencies} == {"ok"}

    def test_failed_embedding_drives_status_down(self, tmp_path) -> None:
        broken = SystemService(
            Settings(), embedding=_FailEmbedding(),
            vector_store=_OkStore(), db=_OkDb(), data_dir=str(tmp_path),
        )
        health = broken.get_health()
        assert health.status == "down"
        embedding = next(
            d for d in health.dependencies if d.name == "embedding"
        )
        assert embedding.status == "down"


class TestNeverLeaksSecrets:
    def test_info_contains_no_key_or_secret_fields(
        self, svc: SystemService,
    ) -> None:
        info = svc.get_info()
        assert info.providers["llm"].model is not None
        # The view shape has no key/secret attribute at all — assert the
        # surface stays closed (models only, never api keys).
        assert not hasattr(info.providers["llm"], "api_key")
        assert not hasattr(info.providers["llm"], "key")


__all__ = []
