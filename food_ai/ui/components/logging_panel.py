from __future__ import annotations

import streamlit as st

from food_ai.ui.config import REVIEW_STATUS_OPTIONS


def render_logging_panel(notes: str, review_status: str) -> dict[str, object]:
    st.subheader("Log Test")
    notes_value = st.text_area("Test Notes", value=notes, height=100)
    status_value = st.selectbox(
        "Manual Review Status",
        options=list(REVIEW_STATUS_OPTIONS),
        index=list(REVIEW_STATUS_OPTIONS).index(review_status) if review_status in REVIEW_STATUS_OPTIONS else 0,
    )
    save_clicked = st.button("Save Test Log", width="stretch")
    return {
        "notes": notes_value,
        "review_status": status_value,
        "save_clicked": save_clicked,
    }

