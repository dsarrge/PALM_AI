from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st


def render_upload_panel() -> dict[str, Any]:
    st.subheader("Image Input")

    uploaded_file = st.file_uploader(
        "Upload a food image",
        type=["jpg", "jpeg", "png", "webp"],
        help="Upload a local food image for PALM inference.",
    )

    return {"uploaded_file": uploaded_file,}


def render_image_preview(image_path: str | Path | None, max_width: int = 360) -> None:
    st.subheader("Input Image Preview")

    if not image_path:
        st.info("Upload an image to begin.")
        return

    resolved_path = Path(image_path)

    if not resolved_path.exists():
        st.warning(f"Image path is missing: {resolved_path}")
        return

    st.image(str(resolved_path), caption=resolved_path.name, width=max_width)
