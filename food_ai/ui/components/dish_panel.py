from __future__ import annotations

import streamlit as st


def _render_table(title: str, rows: list[dict]) -> None:
    st.markdown(f"**{title}**")
    if not rows:
        st.info(f"No {title.lower()} available.")
        return
    st.dataframe(rows, use_container_width=True)


def render_dish_panel(formatted: dict) -> None:
    dishes = formatted.get("dishes") or {}

    st.caption(
        f"Classifier calibrated: {dishes.get('classifier_calibrated', False)}"
        + (f" | Temperature: {dishes.get('classifier_temperature')}" if dishes.get("classifier_temperature") else "")
    )
    _render_table("Classifier Top-K", dishes.get("classifier") or [])
