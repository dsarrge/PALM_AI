from __future__ import annotations

import html

import streamlit as st


_BADGE_COLORS = {
    "success": ("#e8f7ec", "#1f7a3d"),
    "info": ("#eaf2ff", "#2259b5"),
    "warning": ("#fff3dc", "#a15c00"),
    "error": ("#fdeaea", "#b42318"),
}


def _badge(label: str, level: str) -> str:
    background, foreground = _BADGE_COLORS.get(level, _BADGE_COLORS["info"])
    safe_label = html.escape(label)
    return (
        f"<span style='display:inline-block;margin:0 0.35rem 0.35rem 0;padding:0.2rem 0.55rem;"
        f"border-radius:999px;background:{background};color:{foreground};font-size:0.9rem;font-weight:600;'>"
        f"{safe_label}</span>"
    )


def render_status_panel(formatted: dict) -> None:
    # summary  = formatted.get("summary") or {}
    pipeline = formatted.get("pipeline") or {}
    statuses = formatted.get("statuses") or []

    st.subheader("Pipeline Status")
    if statuses:
        st.markdown("".join(_badge(item.get("label", "status"), item.get("level", "info")) for item in statuses), unsafe_allow_html=True)

    columns = st.columns(2)
    columns[0].metric("Gate", (pipeline.get("gate") or {}).get("label", "unavailable"))
    columns[1].metric("Inference Mode", pipeline.get("inference_mode", "unknown"))
    # columns[2].metric("Selected Dish", pipeline.get("selected_dish", "n/a"))
    # columns[3].metric("Overall Confidence", summary.get("overall_confidence", "n/a"))

    if pipeline.get("warnings"):
        st.warning("Warnings / fallbacks: " + ", ".join(pipeline.get("warnings") or []))
    if pipeline.get("artifacts_note"):
        st.info(pipeline["artifacts_note"])
    if pipeline.get("gate_reason"):
        st.caption(pipeline["gate_reason"])
