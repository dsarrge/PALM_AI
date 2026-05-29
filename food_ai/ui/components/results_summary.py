from __future__ import annotations

import streamlit as st


def render_results_summary(formatted: dict) -> None:
    summary = formatted.get("summary") or {}
    st.subheader("Dish Classification / Retrieval")
    columns = st.columns(3)
    columns[0].metric("Top Dish", summary.get("top_dish", "n/a"))
    columns[1].metric("Overall Confidence", summary.get("overall_confidence", "n/a"))
    columns[2].metric("PALM State", summary.get("ready_state", "n/a"))
    st.write(summary.get("summary_sentence", "No inference summary available."))
