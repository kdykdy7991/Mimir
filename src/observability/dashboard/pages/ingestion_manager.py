"""
Ingestion manager page (G4) — file upload + progress + delete.

Three panels:

1. Upload: a file uploader that writes the file to a tmp
   path and triggers ``IngestionService.ingest_uploaded_file``.
2. Progress: ``st.progress`` updated by the pipeline's
   ``on_progress`` callback.
3. Existing documents: list + delete buttons calling
   ``DocumentManager.delete_document``.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from src.observability.dashboard.services.data_service import DataService
from src.observability.dashboard.services.ingestion_service import (
    IngestionService,
    IngestionServiceError,
)


def _ingestion_service() -> IngestionService:
    if "ingestion_service" not in st.session_state:
        st.session_state["ingestion_service"] = IngestionService()
    return st.session_state["ingestion_service"]


def _data_service() -> DataService | None:
    """Reuse the data-browser's service if it's already built,
    otherwise build one here (cache-bypass).
    """
    if "data_service" in st.session_state:
        return st.session_state["data_service"]
    # Try the same builder the data browser uses.
    try:
        # M1: build the DocumentManager through the application-layer
        # composition root so the dashboard shares the same storage
        # assembly as CLI / MCP / Web API.
        from src.application.composition import build_document_manager
        from src.core.settings import load_settings
        from src.ingestion.storage.image_storage import ImageStorage
        from src.libs.vector_store import VectorStoreFactory

        settings = load_settings()
        chroma = VectorStoreFactory.create(settings.vector_store)
        manager = build_document_manager(
            data_dir="data", settings=settings, vector_store=chroma,
        )
        # Preview image storage uses the same default layout as the
        # manager's own store (./data/db + ./data/images).
        image_storage = ImageStorage()
        svc = DataService(manager=manager, images=image_storage)
        st.session_state["data_service"] = svc
        return svc
    except Exception:  # noqa: BLE001
        return None


def _render_upload(svc: IngestionService) -> None:
    """Upload panel — file uploader + ingest button + progress."""
    st.subheader("上传文件")
    collection = st.text_input("集合名", value="default")
    force_reingest = st.checkbox(
        "强制重新入库（忽略 hash 去重，用于测试配置变更）",
        value=False,
        help="勾选后会覆盖该文件已有的入库记录和向量/图片数据。",
    )

    uploaded = st.file_uploader(
        "选择 PDF 文件",
        type=["pdf"],
        accept_multiple_files=False,
    )
    if uploaded is None:
        return

    if st.button("开始入库", type="primary"):
        # Stage the upload to a tmp file the pipeline can read.
        staged_path: str | None = None
        try:
            staged_path = svc.stage_ingested_file(
                uploaded.getvalue(), suffix=".pdf",
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"保存上传文件失败：{exc}")
            return

        progress_bar = st.progress(0.0, text="准备中…")
        status_text = st.empty()

        def on_progress(stage: str, current: int, total: int) -> None:
            pct = (current / total) if total else 0.0
            progress_bar.progress(
                min(1.0, max(0.0, pct)),
                text=f"阶段：{stage} ({current}/{total})",
            )

        try:
            result = svc.ingest_uploaded_file(
                staged_path,
                collection=collection,
                on_progress=on_progress,
                force=force_reingest,
            )
        except IngestionServiceError as exc:
            status_text.error(f"入库失败：{exc}")
            return
        finally:
            if staged_path is not None:
                svc.cleanup_staged_file(staged_path)

        # Render result
        status = result.get("status")
        if status == "ok":
            progress_bar.progress(1.0, text="完成")
            status_text.success(f"入库完成：{result.get('source')}")
            stages = result.get("stages") or {}
            if stages:
                with st.expander("阶段耗时", expanded=False):
                    for name, info in stages.items():
                        elapsed = (info or {}).get("elapsed_ms")
                        st.write(
                            f"- **{name}**: "
                            f"{elapsed:.1f} ms"
                            if isinstance(elapsed, (int, float))
                            else f"- **{name}**: —",
                        )
            # Invalidate the cached data service so the
            # data-browser shows the new doc next time the
            # user navigates there.
            st.session_state.pop("data_service", None)
        elif status == "skipped":
            progress_bar.progress(1.0, text="跳过")
            status_text.info("该文档已入库（hash 已存在）。")
        else:
            progress_bar.progress(1.0, text="失败")
            status_text.error(f"入库失败：{result.get('error')}")


def _render_documents(svc: DataService | None) -> None:
    """Existing documents with delete buttons."""
    st.divider()
    st.subheader("已入库文档")
    if svc is None:
        st.info("无法读取文档列表。")
        return
    docs = svc.list_documents()
    if not docs:
        st.info("还没有入库的文档。")
        return

    for d in docs:
        cols = st.columns([6, 1, 1])
        cols[0].code(d.source_path, language="text")
        cols[1].write(f"{d.n_chunks} 个分块")
        if cols[2].button("删除", key=f"del-{d.source_path}"):
            try:
                result = svc._manager.delete_document(d.source_path)
                st.success(
                    f"已删除 {d.source_path} "
                    f"(chunks={result.chunks_deleted}, "
                    f"images={result.images_deleted}, "
                    f"bm25={result.bm25_removed}, "
                    f"errors={len(result.errors)})"
                )
                # Force re-render
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"删除失败：{exc}")


def page() -> None:
    st.title("📥 文档入库")
    try:
        svc = _ingestion_service()
    except Exception as exc:  # noqa: BLE001
        st.error(f"无法初始化 IngestionService：{exc}")
        return

    _render_upload(svc)
    _render_documents(_data_service())


__all__ = ["page"]
