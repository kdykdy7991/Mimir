"""
Dashboard pages — each module exposes a ``page`` attribute
(Streamlit ``Page`` or callable) used by ``app.py``'s
``st.navigation``.
"""

from src.observability.dashboard.pages import (
    data_browser,
    ingestion_manager,
    ingestion_traces,
    overview,
    query_traces,
)

__all__ = [
    "data_browser",
    "ingestion_manager",
    "ingestion_traces",
    "overview",
    "query_traces",
]
