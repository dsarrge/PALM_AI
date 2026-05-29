from __future__ import annotations

from pathlib import Path

import streamlit as st


def render_image_preview(image_path: str | Path | None) -> None:
    st.subheader("Input Image Preview")
    if not image_path:
        st.info("Upload an image or load one of the local sample cases to begin.")
        return
    resolved_path = Path(image_path)
    if not resolved_path.exists():
        st.warning(f"Image path is missing: {resolved_path}")
        return
    st.image(str(resolved_path), caption=resolved_path.name, width="stretch")

