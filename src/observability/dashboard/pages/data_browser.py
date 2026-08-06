"""
Data browser page (G3) — read-only document / chunk
explorer.

Renders three stacked sections:

1. Collection summary (cards from :class:`DataService`)
2. Document table (one row per ingested file)
3. Chunk detail (when a row is clicked — full chunk
   text + metadata + image previews)
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from src.observability.dashboard.services.data_service import DataService


def _service() -> DataService | None:
    """Build / fetch a cached DataService.

    Streamlit re-runs the page on every interaction, so we
    stash the service in ``st.session_state`` to avoid
    rebuilding the (potentially expensive) storage stack.

    If the storage stack isn't wired (typical for first
    launch without data), we render a friendly hint instead
    of crashing.
    """
    if "data_service" in st.session_state:
        return st.session_state["data_service"]

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
        # manager's own store (./data/db + ./data/images) — kept as a
        # separate instance so DataService stays a page facade.
        image_storage = ImageStorage()
        svc = DataService(manager=manager, images=image_storage)
        st.session_state["data_service"] = svc
        return svc
    except Exception as exc:  # noqa: BLE001
        st.error(f"无法初始化 DataService：{exc}")
        st.caption(
            "提示：先在「文档入库」页 ingest 至少一个文档，"
            "然后回来刷新。",
        )
        return None


def _render_summary(svc: DataService) -> None:
    """Top-of-page collection summary card."""
    try:
        stats = svc.get_collection_stats()
    except Exception as exc:  # noqa: BLE001
        st.warning(f"无法读取 collection 统计：{exc}")
        return
    cols = st.columns(4)
    cols[0].metric("集合", stats.collection)
    cols[1].metric("文档数", stats.n_documents)
    cols[2].metric("分块数", stats.n_chunks)
    cols[3].metric("图片数", stats.n_images)


def _render_table(svc: DataService) -> str | None:
    """Document list — return the source_path of the row the
    user expanded, if any."""
    docs = svc.list_documents()
    if not docs:
        st.info(
            "还没有入库的文档。请先到「文档入库」页上传文件。",
            icon="📭",
        )
        return None

    rows: list[dict[str, Any]] = []
    for d in docs:
        rows.append({
            "source": d.source_path,
            "collection": d.collection,
            "chunks": d.n_chunks,
            "images": d.n_images,
            "size_kb": (
                round(d.file_size / 1024, 1)
                if d.file_size is not None else "—"
            ),
            "status": d.status,
        })
    st.dataframe(
        rows,
        hide_index=True,
        width='stretch',
        column_config={
            "source": st.column_config.TextColumn("文档路径", width="large"),
            "collection": st.column_config.TextColumn("集合", width="small"),
            "chunks": st.column_config.NumberColumn("分块数", width="small"),
            "images": st.column_config.NumberColumn("图片", width="small"),
            "size_kb": st.column_config.TextColumn("大小 (KB)"),
            "status": st.column_config.TextColumn("状态"),
        },
    )
    # Streamlit's dataframe doesn't expose row-clicks easily;
    # offer a selectbox as a fallback.
    return st.selectbox(
        "选择一个文档查看详情",
        options=[d.source_path for d in docs],
        index=None,
        placeholder="—",
    )


def _render_detail(svc: DataService, source_path: str) -> None:
    detail = svc.get_document_detail(source_path)
    if detail is None:
        st.warning(f"找不到文档：{source_path}")
        return

    st.subheader(source_path)
    st.caption(
        f"分块：{detail.info.n_chunks} · "
        f"图片: {detail.info.n_images} · "
        f"状态: {detail.info.status}"
    )

    # Chunks
    st.markdown("#### 分块")
    if not detail.chunks:
        st.info("此文档没有分块。")
    else:
        for i, c in enumerate(detail.chunks, start=1):
            with st.expander(
                f"#{i} · {c.get('id', '?')[:12]}…",
                expanded=(i == 1),
            ):
                st.text(c.get("text", ""))
                meta = c.get("metadata", {})
                if meta:
                    st.markdown("**元数据**")
                    st.code(
                        json.dumps(meta, indent=2, ensure_ascii=False),
                        language="json",
                    )

    # Image previews
    if detail.images:
        st.markdown("#### 关联图片")
        cols = st.columns(min(3, len(detail.images)))
        for i, img in enumerate(detail.images):
            img_id = getattr(img, "image_id", None)
            if not img_id:
                continue
            b64 = svc.load_image_b64(img_id)
            with cols[i % len(cols)]:
                if b64 is None:
                    st.warning(f"图片 {img_id} 不可读")
                else:
                    st.image(
                        f"data:image/png;base64,{b64}",
                        caption=img_id,
                        width='stretch',
                    )


def page() -> None:
    st.title("🗂️ 数据浏览器")
    svc = _service()
    if svc is None:
        return

    _render_summary(svc)

    st.divider()
    st.subheader("文档列表")
    selected = _render_table(svc)

    if selected:
        st.divider()
        _render_detail(svc, selected)


__all__ = ["page"]
