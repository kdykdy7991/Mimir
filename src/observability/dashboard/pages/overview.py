"""
Overview page (G1) — system summary cards.

Renders one :class:`ComponentCard` per configured backend
(LLM, Embedding, Vector Store, etc.) plus a one-shot
collection stats summary if a :class:`DocumentManager` is
available in the session.

Each LLM / Embedding card also exposes a connectivity probe
button so users can quickly verify that the configured
OpenAI-compatible endpoints are reachable.
"""

from __future__ import annotations

import time

import streamlit as st

from src.core.settings import Settings
from src.observability.dashboard.services.config_service import (
    ComponentCard,
    ConfigService,
)
from src.observability.dashboard.services.health_service import (
    HealthResult,
    check_openai_compatible,
)


# Seconds a user must wait between two consecutive health probes
# for the same service. Prevents accidental API spamming.
_COOLDOWN_SECONDS = 5.0

# session_state keys per service name.
_HEALTH_KEYS = {
    "LLM": ("llm_last_test", "llm_test_result"),
    "Embedding": ("embedding_last_test", "embedding_test_result"),
}


def _service_config(name: str, settings: Settings) -> tuple[str, str, str]:
    """Return (base_url, model, api_key) for LLM or Embedding."""
    if name == "LLM":
        return (
            settings.llm.base_url or "",
            settings.llm.model or "",
            settings.llm.api_key or "",
        )
    if name == "Embedding":
        return (
            settings.embedding.base_url or "",
            settings.embedding.model or "",
            settings.embedding.api_key or "",
        )
    return "", "", ""


def _render_health_button(card: ComponentCard, settings: Settings) -> None:
    """Render a '测试连通' button + result area for LLM / Embedding."""
    last_key, result_key = _HEALTH_KEYS[card.name]
    last_test = st.session_state.get(last_key, 0.0)
    now = time.time()
    cooldown_remaining = _COOLDOWN_SECONDS - (now - last_test)
    can_test = cooldown_remaining <= 0

    cols = st.columns([1, 2])
    with cols[0]:
        clicked = st.button(
            "测试连通",
            key=f"health-btn-{card.name}",
            disabled=not can_test,
            type="secondary",
        )

    with cols[1]:
        if not can_test:
            st.caption(f"冷却中，请 {cooldown_remaining:.0f} 秒后重试")

        result = st.session_state.get(result_key)
        if isinstance(result, HealthResult):
            if result.ok:
                st.success(result.message, icon="✅")
            else:
                st.error(result.message, icon="❌")

    if clicked:
        base_url, model, api_key = _service_config(card.name, settings)
        st.session_state[last_key] = time.time()
        if not base_url:
            st.session_state[result_key] = HealthResult(
                ok=False,
                message="未配置 base_url，无法检测",
            )
        else:
            with st.spinner("测试中..."):
                st.session_state[result_key] = check_openai_compatible(
                    base_url=base_url,
                    expected_model=model or None,
                    api_key=api_key,
                )
        st.rerun()


def _format_card(c: ComponentCard, settings: Settings) -> None:
    """Render a single component card."""
    status_icon = "🟢" if c.available else "⚪"
    with st.container(border=True):
        st.markdown(f"### {status_icon} {c.name}")
        st.caption(c.summary or "—")
        if c.name in _HEALTH_KEYS:
            _render_health_button(c, settings)
        if c.extra:
            # Render extras as a small key-value table.
            rows = "\n".join(
                f"- **{k}**: `{v}`"
                for k, v in c.extra.items()
                if v not in (None, "", 0)
            )
            if rows:
                st.markdown(rows)


def page() -> None:
    st.title("📊 系统总览")
    st.caption(
        "当前配置一览 + 数据统计。所有内容从 ``config/settings.yaml`` 实时读取，"
        "修改后刷新页面即可生效。"
    )

    cfg = ConfigService.from_settings_path()
    settings = cfg.settings

    cols = st.columns(3)
    cards = cfg.cards()
    for i, card in enumerate(cards):
        with cols[i % 3]:
            _format_card(card, settings)

    with st.expander("查看完整 settings 路径与原始 YAML", expanded=False):
        st.code(str(settings.model_dump_json(indent=2)),
                language="json")


__all__ = ["page"]
