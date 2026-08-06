"""
Query trace page (G6) — query history + per-stage timing +
dense/sparse comparison.

Mirrors the ingestion trace page but filters by
``trace_type == "query"`` and adds:

* a search box (keyword match against any event detail —
  typically the user's query string)
* dense vs sparse side-by-side comparison when the trace
  has both stages
"""

from __future__ import annotations

import streamlit as st

from src.observability.dashboard.services.trace_service import (
    TRACE_TYPE_QUERY,
    TraceService,
)


def _service() -> TraceService:
    if "trace_service" not in st.session_state:
        st.session_state["trace_service"] = TraceService("./logs/traces.jsonl")
    return st.session_state["trace_service"]


def _render_history(svc: TraceService, keyword: str | None):
    records = svc.search(trace_type=TRACE_TYPE_QUERY, keyword=keyword)
    if not records:
        st.info("还没有查询追踪记录。", icon="📭")
        return None

    rows = [
        {
            "trace_id": r.trace_id[:8] + "…",
            "started_at": r.started_at,
            "total_ms": round(r.total_elapsed_ms, 1),
            "stages": ", ".join(s.name for s in r.stages),
        }
        for r in records
    ]
    st.dataframe(rows, hide_index=True, width='stretch')
    return st.selectbox(
        "选择一条 query trace 查看详情",
        options=[r.trace_id for r in records],
        index=None,
        placeholder="—",
    )


def _render_dense_vs_sparse(rec):
    """Render a side-by-side comparison if both stages are present."""
    stages = {s.name: s for s in rec.stages}
    if "dense_retrieval" not in stages or "sparse_retrieval" not in stages:
        return

    dense = stages["dense_retrieval"]
    sparse = stages["sparse_retrieval"]
    cols = st.columns(2)
    with cols[0]:
        st.markdown("##### Dense")
        st.metric("耗时", f"{dense.elapsed_ms:.1f}ms")
        for ev in dense.events:
            if "n_in" in ev.details or "n_out" in ev.details:
                st.write(
                    f"- `{ev.event}` — "
                    f"in={ev.details.get('n_in', '?')}, "
                    f"out={ev.details.get('n_out', '?')}"
                )
    with cols[1]:
        st.markdown("##### Sparse")
        st.metric("耗时", f"{sparse.elapsed_ms:.1f}ms")
        for ev in sparse.events:
            if "n_in" in ev.details or "n_out" in ev.details:
                st.write(
                    f"- `{ev.event}` — "
                    f"in={ev.details.get('n_in', '?')}, "
                    f"out={ev.details.get('n_out', '?')}"
                )


def _render_rerank(rec):
    """If the trace has a rerank stage, render a small event log."""
    rerank = next((s for s in rec.stages if s.name == "rerank"), None)
    if rerank is None:
        return
    st.markdown("##### Rerank")
    for ev in rerank.events:
        if ev.event == "fallback":
            st.warning(f"⚠️ Rerank fallback: {ev.details}")
        else:
            st.write(
                f"- `{ev.event}`"
                + (f" — {ev.elapsed_ms:.1f}ms"
                   if ev.elapsed_ms is not None else "")
            )


def _render_detail(svc: TraceService, trace_id: str):
    rec = svc.get(trace_id)
    if rec is None:
        st.warning(f"找不到 trace: {trace_id}")
        return

    st.subheader(f"Query Trace {trace_id[:12]}…")
    st.caption(
        f"total={rec.total_elapsed_ms:.1f}ms · "
        f"stages={', '.join(s.name for s in rec.stages)}"
    )

    if not rec.stages:
        st.info("此 trace 没有 stage 数据。")
        return

    # Per-stage timing waterfall.
    chart_data = {
        "stage": [s.name for s in rec.stages],
        "elapsed_ms": [s.elapsed_ms for s in rec.stages],
    }
    st.bar_chart(chart_data, x="stage", y="elapsed_ms")

    _render_dense_vs_sparse(rec)
    _render_rerank(rec)

    # All stage events, for debugging
    with st.expander("全部 stage 事件", expanded=False):
        for s in rec.stages:
            st.markdown(f"**{s.name}**")
            for ev in s.events:
                st.write(
                    f"- `{ev.event}`"
                    + (f" — {ev.elapsed_ms:.1f}ms"
                       if ev.elapsed_ms is not None else "")
                    + (f" — `{ev.details}`"
                       if ev.details else "")
                )


def page() -> None:
    st.title("🔎 查询追踪")
    svc = _service()
    keyword = st.text_input("关键词搜索（可选）", value="")
    selected = _render_history(svc, keyword or None)
    if selected:
        st.divider()
        _render_detail(svc, selected)


__all__ = ["page"]
