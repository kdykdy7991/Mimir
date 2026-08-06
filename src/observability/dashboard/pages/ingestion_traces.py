"""
Ingestion trace page (G5) — ingestion history + per-stage
timing waterfall.

Renders two stacked sections:

1. Trace history list (newest first) with a row per
   ``trace_type == "ingestion"`` record.
2. Selected trace detail: per-stage bar chart of
   ``elapsed_ms`` and a stage-by-stage event log.
"""

from __future__ import annotations

import streamlit as st

from src.observability.dashboard.services.trace_service import (
    TRACE_TYPE_INGESTION,
    TraceService,
)


def _service() -> TraceService:
    if "trace_service" not in st.session_state:
        # Default path matches the smoke config + scripts/ingest.py.
        st.session_state["trace_service"] = TraceService("./logs/traces.jsonl")
    return st.session_state["trace_service"]


def _render_history(svc: TraceService):
    records = svc.list_recent(trace_type=TRACE_TYPE_INGESTION)
    if not records:
        st.info("还没有入库追踪记录。", icon="📭")
        return None
    rows = [
        {
            "trace_id": r.trace_id[:8] + "…",
            "started_at": r.started_at,
            "total_ms": round(r.total_elapsed_ms, 1),
            "n_stages": len(r.stages),
        }
        for r in records
    ]
    st.dataframe(rows, hide_index=True, width='stretch')
    return st.selectbox(
        "选择一条 trace 查看详情",
        options=[r.trace_id for r in records],
        index=None,
        placeholder="—",
    )


def _render_detail(svc: TraceService, trace_id: str):
    rec = svc.get(trace_id)
    if rec is None:
        st.warning(f"找不到 trace: {trace_id}")
        return

    st.subheader(f"Trace {trace_id[:12]}…")
    st.caption(
        f"{rec.trace_type} · "
        f"started_at={rec.started_at:.0f} · "
        f"total={rec.total_elapsed_ms:.1f}ms"
    )

    if not rec.stages:
        st.info("此 trace 没有 stage 数据。")
        return

    # Waterfall: bar chart of elapsed_ms per stage.
    chart_data = {
        "stage": [s.name for s in rec.stages],
        "elapsed_ms": [s.elapsed_ms for s in rec.stages],
    }
    st.bar_chart(chart_data, x="stage", y="elapsed_ms")

    # Stage detail (per-stage event log)
    st.markdown("#### Stage 详情")
    for s in rec.stages:
        with st.expander(
            f"{s.name} — {s.elapsed_ms:.1f}ms",
            expanded=False,
        ):
            if not s.events:
                st.write("（无事件）")
                continue
            for ev in s.events:
                st.markdown(
                    f"- `{ev.event}`"
                    + (f" — {ev.elapsed_ms:.1f}ms"
                       if ev.elapsed_ms is not None else "")
                    + (f" — `{ev.details}`"
                       if ev.details else "")
                )


def page() -> None:
    st.title("🔍 入库追踪")
    svc = _service()
    selected = _render_history(svc)
    if selected:
        st.divider()
        _render_detail(svc, selected)


__all__ = ["page"]
