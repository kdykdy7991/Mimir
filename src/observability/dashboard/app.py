"""
Dashboard entry point (G1).

Multi-page Streamlit app using ``st.navigation`` (Streamlit
>= 1.30). Six pages are registered; pages that haven't been
implemented yet show a friendly placeholder so the navigation
works end-to-end while the rest of G is being filled in.

Run with::

    streamlit run src/observability/dashboard/app.py
"""

from __future__ import annotations

import streamlit as st

from src.observability.dashboard.pages import (
    data_browser,
    ingestion_manager,
    ingestion_traces,
    overview,
    query_traces,
)


PAGES = [
    st.Page(overview.page, title="系统总览", default=True),
    st.Page(data_browser.page, title="数据浏览器", url_path="data-browser"),
    st.Page(ingestion_manager.page, title="文档入库", url_path="ingestion-manager"),
    st.Page(ingestion_traces.page, title="入库追踪", url_path="ingestion-traces"),
    st.Page(query_traces.page, title="查询追踪", url_path="query-traces"),
]


def _setup_page() -> None:
    """Common page config — runs once per app instance."""
    st.set_page_config(
        page_title="Modular RAG Dashboard",
        page_icon="📚",
        layout="wide",
    )


def main() -> None:
    _setup_page()
    nav = st.navigation(PAGES, position="sidebar")
    nav.run()


if __name__ == "__main__":
    main()
else:
    # ``streamlit run`` imports this module rather than
    # executing it as __main__. Call ``main`` so the page
    # config + navigation are wired on every run.
    main()
