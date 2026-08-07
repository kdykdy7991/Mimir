"""
``SystemService`` — application-layer entry point for service metadata &
dependency health.

职责

- 从 ``Settings`` 派生 ``/system/info``（Provider 配置 + 模型名）。
- 真实探测依赖并派生 ``/system/health``（M3 收尾）：

  - ``embedding`` — 实际调用一次 ``embed_single("ping")``（本地/远端 API），
    成功 → ``ok``，抛 ``EmbeddingError`` → ``down``；
  - ``chroma`` — 对 ``MultiCollectionVectorStore`` 底层的 PersistentClient
    执行 ``heartbeat()``；
  - ``sqlite`` — 对 Web API 的 ``WebApiDB`` 执行 ``SELECT 1``；
  - ``bm25`` — 校验 ``<data_dir>/db`` 可写（稀疏索引为本地落盘）。

  探测结果带 5s TTL 缓存，避免每次 /system/health、/system/info 都打一次
  embedding API。可选 Provider（llm / vision）不在检索热路径上，其
  ``ready`` 反映「配置存在」而非网络探测。

- 永不返回任何 Provider API key 或 secret。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from src.core.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class ProviderStatusView:
    """Per-Provider presence + readiness view for the API.

    Mirrors ``src.web_api.schemas.system.ProviderStatus`` but lives in
    the application layer so the Web API just maps it to the DTO
    without inspecting the raw settings object.
    """

    configured: bool
    ready: bool
    model: str | None


@dataclass
class SystemInfoView:
    """Application-layer ``/system/info`` payload.

    Same shape as ``src.web_api.schemas.system.SystemInfo``. Kept as a
    plain dataclass here (no Pydantic) so the service layer stays
    transport-agnostic — the Web API layer converts to DTO.
    """

    app_name: str
    version: str
    storage_backend: str
    sparse_backend: str
    rerank_backend: str
    providers: dict[str, ProviderStatusView]
    features: dict[str, bool] = field(default_factory=dict)


@dataclass
class DependencyHealthView:
    """One dependency's health row."""

    name: str
    status: str  # "ok" | "degraded" | "down"
    detail: str | None = None


@dataclass
class SystemHealthView:
    """Application-layer ``/system/health`` payload."""

    status: str  # worst of dependencies
    dependencies: list[DependencyHealthView] = field(default_factory=list)


_STATUS_ORDER = {"down": 3, "degraded": 2, "ok": 1}


def _worst_status(statuses: list[str]) -> str:
    """Top-level health = the worst of any dependency."""
    if not statuses:
        return "ok"
    return max(statuses, key=lambda s: _STATUS_ORDER.get(s, 0))


class SystemService:
    """Read-only view of service metadata & dependency health."""

    APP_NAME = "skdy-rag-server"
    VERSION = "0.2.0"

    # Probe results are cached briefly so repeated /system/info +
    # /system/health calls don't hammer the embedding provider.
    _PROBE_TTL_SECONDS = 5.0

    def __init__(
        self,
        settings: "Settings",
        *,
        embedding: Any | None = None,
        vector_store: Any | None = None,
        db: Any | None = None,
        data_dir: str | None = None,
    ) -> None:
        self._settings = settings
        # Optional real collaborators (Web API composition injects them;
        # tests / CLI can leave them None → probes report "degraded").
        self._embedding = embedding
        self._vector_store = vector_store
        self._db = db
        self._data_dir = data_dir
        self._probe_cache: dict[str, tuple[float, DependencyHealthView]] = {}

    @property
    def settings(self) -> "Settings":
        return self._settings

    # ------------------------------------------------------------------
    # /system/info
    # ------------------------------------------------------------------
    def get_info(self) -> SystemInfoView:
        """Return service + provider status snapshot.

        ``ready`` is real for embedding (probe-based). ``llm`` / ``vision``
        are optional and not in the retrieval hot path, so their ``ready``
        mirrors configuration presence — never ``True`` when unconfigured.
        """
        embedding_probe = self._probe("embedding", self._probe_embedding)
        llm_configured = self._is_provider_configured("llm")
        vision_configured = self._is_provider_configured("vision")
        embedding_configured = self._is_provider_configured("embedding")
        providers = {
            "llm": ProviderStatusView(
                configured=llm_configured,
                ready=llm_configured,
                model=self._get_provider_model("llm"),
            ),
            "embedding": ProviderStatusView(
                configured=embedding_configured,
                ready=embedding_probe.status == "ok",
                model=self._get_provider_model("embedding"),
            ),
            "vision": ProviderStatusView(
                configured=vision_configured,
                ready=vision_configured,
                model=self._get_provider_model("vision"),
            ),
        }
        return SystemInfoView(
            app_name=self.APP_NAME,
            version=self.VERSION,
            storage_backend=getattr(
                self._settings.vector_store, "backend", "unknown",
            ),
            sparse_backend=self._sparse_backend_name(),
            rerank_backend=getattr(
                self._settings.rerank, "backend", "none",
            ),
            providers=providers,
            features={},
        )

    # ------------------------------------------------------------------
    # /system/health
    # ------------------------------------------------------------------
    def get_health(self) -> SystemHealthView:
        """Return a real dependency health snapshot."""
        deps = [
            self._probe("embedding", self._probe_embedding),
            self._probe("chroma", self._probe_chroma),
            self._probe("sqlite", self._probe_sqlite),
            self._probe("bm25", self._probe_bm25),
        ]
        return SystemHealthView(
            status=_worst_status([d.status for d in deps]),
            dependencies=deps,
        )

    # ------------------------------------------------------------------
    # Probes
    # ------------------------------------------------------------------
    def _probe(
        self, key: str, fn: Callable[[], DependencyHealthView],
    ) -> DependencyHealthView:
        """Run ``fn`` with a short TTL cache."""
        now = time.monotonic()
        hit = self._probe_cache.get(key)
        if hit is not None and now - hit[0] < self._PROBE_TTL_SECONDS:
            return hit[1]
        result = fn()
        self._probe_cache[key] = (now, result)
        return result

    def _probe_embedding(self) -> DependencyHealthView:
        name = "embedding"
        if not self._is_provider_configured("embedding"):
            return DependencyHealthView(
                name, "down", "embedding provider not configured",
            )
        if self._embedding is None:
            return DependencyHealthView(
                name, "degraded", "embedding client not wired",
            )
        try:
            # PRD §5.2/§3.1: health checks must never count as business
            # usage. Tag the probe so the usage store can drop it.
            from src.libs.embedding.usage import embedding_usage_context

            with embedding_usage_context(operation="healthcheck"):
                self._embedding.embed_single("ping")
            return DependencyHealthView(name, "ok")
        except Exception as exc:  # noqa: BLE001 — any failure → down
            from src.libs.embedding import EmbeddingError

            if isinstance(exc, EmbeddingError):
                return DependencyHealthView(name, "down", str(exc)[:200])
            return DependencyHealthView(name, "down", type(exc).__name__)

    def _probe_chroma(self) -> DependencyHealthView:
        name = "chroma"
        if self._vector_store is None:
            return DependencyHealthView(
                name, "degraded", "vector store not wired",
            )
        try:
            self._vector_store.heartbeat()
            return DependencyHealthView(name, "ok")
        except Exception as exc:  # noqa: BLE001
            return DependencyHealthView(name, "down", type(exc).__name__)

    def _probe_sqlite(self) -> DependencyHealthView:
        name = "sqlite"
        if self._db is None:
            return DependencyHealthView(
                name, "degraded", "web_api db not wired",
            )
        try:
            self._db.ping()
            return DependencyHealthView(name, "ok")
        except Exception as exc:  # noqa: BLE001
            return DependencyHealthView(name, "down", type(exc).__name__)

    def _probe_bm25(self) -> DependencyHealthView:
        name = "bm25"
        if not self._data_dir:
            return DependencyHealthView(
                name, "degraded", "data dir not provided",
            )
        try:
            # Sparse retrieval is local on-disk; reachability == the data
            # dir being writable.
            db_dir = Path(self._data_dir) / "db"
            db_dir.mkdir(parents=True, exist_ok=True)
            return DependencyHealthView(name, "ok")
        except OSError as exc:  # noqa: BLE001
            return DependencyHealthView(name, "down", str(exc)[:200])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _is_provider_configured(self, name: str) -> bool:
        """A provider counts as configured if it has a model resolved.

        We don't read the API key here — only whether one is *present* in
        the environment / settings.
        """
        return bool(self._get_provider_model(name))

    def _get_provider_model(self, name: str) -> str | None:
        if name == "llm":
            return getattr(self._settings.llm, "model", None)
        if name == "embedding":
            return getattr(self._settings.embedding, "model", None)
        if name == "vision":
            return getattr(
                getattr(self._settings, "vision", None), "model", None,
            )
        return None

    def _sparse_backend_name(self) -> str:
        """``SparseSettings`` may not expose ``backend`` directly; probe safely."""
        sparse = getattr(self._settings, "sparse", None)
        for attr in ("backend", "kind", "type"):
            value = getattr(sparse, attr, None)
            if value:
                return str(value)
        return "bm25"


__all__ = [
    "DependencyHealthView",
    "ProviderStatusView",
    "SystemHealthView",
    "SystemInfoView",
    "SystemService",
]
