"""
IngestionService (G4) — thin facade for the ingestion-manager
page.

Wraps :func:`scripts.ingest.build_pipeline` so the page
module can call a single ``ingest_file(path) -> dict`` and
get back per-stage timing + error info.

Why a service at all?
----------------------
The page shouldn't know about ``IngestionPipeline`` or
``build_pipeline`` — those are plumbing. The service isolates
the CLI dependencies and gives the page a single,
test-friendly surface.
"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# Type for the on_progress callback (stage, current, total).
ProgressFn = Callable[[str, int, int], None]


class IngestionServiceError(RuntimeError):
    """Raised when the service can't initialise or run."""


def _ensure_scripts_importable() -> None:
    """Make sure the project root is on ``sys.path``.

    ``scripts/`` sits at the project root (next to ``src/``). When
    Streamlit is launched from a different working directory, Python
    may not see it. We add the project root explicitly so the lazy
    ``from scripts.ingest import build_pipeline`` below never fails.
    """
    # ingestion_service.py is at src/observability/dashboard/services/
    project_root = Path(__file__).resolve().parents[4]
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


class IngestionService:
    """
    Build pipelines on demand and run them against uploaded
    files.

    Usage::

        svc = IngestionService(settings_path="./config/settings.yaml")
        def on_progress(stage, current, total):
            st.progress(current / total, text=stage)
        result = svc.ingest_uploaded_file(
            "/tmp/uploaded.pdf",
            collection="default",
            on_progress=on_progress,
        )
    """

    def __init__(
        self,
        *,
        settings_path: str | None = None,
        data_dir: str | None = None,
    ) -> None:
        # Default paths are resolved relative to the project root so
        # the dashboard works regardless of the directory from which
        # ``streamlit run`` was launched.
        project_root = Path(__file__).resolve().parents[4]
        if settings_path is None:
            settings_path = str(project_root / "config" / "settings.yaml")
        if data_dir is None:
            data_dir = str(project_root / "data")
        self._settings_path = settings_path
        self._data_dir = data_dir

    def _build_pipeline(self, collection: str) -> Any:
        """Lazy import — keep the page import graph clean."""
        _ensure_scripts_importable()
        try:
            from scripts.ingest import build_pipeline
        except ImportError as exc:  # pragma: no cover
            raise IngestionServiceError(
                f"无法导入 build_pipeline: {exc}",
            ) from exc
        from src.core.settings import load_settings
        from src.libs.embedding import EmbeddingFactory
        from src.libs.llm import LLMFactory
        from src.libs.splitter import SplitterFactory
        from src.libs.vector_store import VectorStoreFactory

        settings = load_settings(self._settings_path)
        splitter = SplitterFactory.create(settings.splitter)
        embedding = EmbeddingFactory.create(settings.embedding)
        vector_store = VectorStoreFactory.create(settings.vector_store)
        llm = (
            LLMFactory.create(settings.llm) if settings.llm else None
        )
        return build_pipeline(
            settings=settings,
            data_dir=self._data_dir,
            collection=collection,
            splitter=splitter,
            embedding=embedding,
            vector_store=vector_store,
            llm=llm,
        )

    def ingest_uploaded_file(
        self,
        source: str | Path,
        *,
        collection: str = "default",
        on_progress: ProgressFn | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Run ingestion on a single file.

        ``source`` is a path on disk (the Streamlit
        ``st.file_uploader`` writes the upload to a tempfile
        before we get here). ``on_progress`` is called with
        ``(stage, current, total)`` at each pipeline stage
        transition.

        ``force=True`` clears the integrity record for this
        file before running, so a previously-ingested file is
        re-processed instead of skipped.

        Returns a dict with ``status`` (``"ok"`` / ``"skipped"``
        / ``"failed"``) and per-stage timing pulled from the
        trace context.
        """
        source = Path(source)
        if not source.is_file():
            raise IngestionServiceError(f"文件不存在: {source}")

        pipeline = self._build_pipeline(collection)

        # Force re-ingestion: forget the integrity record first.
        if force:
            try:
                file_hash = pipeline.file_integrity.compute_sha256(
                    str(source),
                )
                if pipeline.file_integrity.forget(file_hash):
                    logger.info(
                        "force re-ingest: cleared integrity for %s",
                        source.name,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "force re-ingest: could not clear integrity: %s",
                    exc,
                )

        # The pipeline expects a real file path. We've already
        # got one (the uploader wrote to a tmp file), so just
        # forward. ``on_progress`` is only passed when set so
        # the pipeline's default (None) is preserved when the
        # page doesn't wire a callback.
        run_kwargs: dict[str, Any] = {
            "path": str(source),
        }
        if on_progress is not None:
            run_kwargs["on_progress"] = on_progress

        try:
            result = pipeline.run(**run_kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ingestion failed for %s", source)
            return {
                "status": "failed",
                "error": str(exc),
                "source": str(source),
            }

        # Pull timing from the pipeline's trace if available.
        trace = getattr(pipeline, "_last_trace", None)
        stages: dict[str, Any] = {}
        if trace is not None:
            try:
                stages = trace.to_dict().get("stages", {})
            except Exception:  # noqa: BLE001
                stages = {}

        return {
            "status": "ok",
            "source": str(source),
            "collection": collection,
            "stages": stages,
        }

    def stage_ingested_file(
        self, file_bytes: bytes, *, suffix: str = ".pdf",
    ) -> Path:
        """
        Write uploaded bytes to a tmp file and return the
        path. Caller is responsible for cleanup.

        Using a tempfile means the dashboard doesn't have to
        know where the user's working dir is — the file
        lifecycle is bounded by the request.
        """
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, dir=tempfile.gettempdir(),
        )
        try:
            tmp.write(file_bytes)
            tmp.flush()
        finally:
            tmp.close()
        return Path(tmp.name)

    def cleanup_staged_file(self, path: str | Path) -> None:
        """Best-effort delete of a staged tmp file."""
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:  # noqa: BLE001
            logger.warning("cleanup failed for %s: %s", path, exc)


__all__ = ["IngestionService", "IngestionServiceError", "ProgressFn"]
