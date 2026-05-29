from __future__ import annotations

import streamlit as st


def render_debug_panel(formatted: dict, show_debug: bool) -> None:
    if not show_debug:
        return
    debug = formatted.get("debug") or {}
    fusion = debug.get("fusion") or {}
    metadata = debug.get("metadata") or {}
    warnings = debug.get("warnings") or []
    runtime = debug.get("runtime") or {}
    usda_status = debug.get("usda_status") or {}
    normalization = debug.get("ingredient_normalization") or []

    st.subheader("Structured JSON / Deep Debug")
    st.write(f"Warnings: {', '.join(warnings) if warnings else 'none'}")
    st.write(f"Fallbacks used: {', '.join(fusion.get('fallbacks_used') or []) if fusion.get('fallbacks_used') else 'none'}")
    st.write(f"Segmentation adapter: {(debug.get('segmentation') or {}).get('adapter', 'n/a')}")
    st.write(f"Inference mode: {metadata.get('inference_mode', 'n/a')}")
    st.write(f"Classifier checkpoint present: {metadata.get('classifier_checkpoint_exists', 'n/a')}")
    st.write(f"Calibration artifact present: {metadata.get('calibration_artifact_exists', 'n/a')}")
    st.write(f"USDA embedding backend: {usda_status.get('embedding_backend', 'unknown')}")
    st.write(f"USDA fallback triggered: {usda_status.get('fallback_triggered', False)}")
    if usda_status.get("fallback_reason"):
        st.warning(f"USDA fallback reason: {usda_status['fallback_reason']}")

    with st.expander("Runtime Diagnostics", expanded=False):
        st.json(runtime)
    with st.expander("USDA / Normalization Status", expanded=False):
        st.json(usda_status)
        if normalization:
            st.dataframe(normalization, use_container_width=True)
        else:
            st.info("No ingredient normalization samples available.")
    with st.expander("Fusion Metadata", expanded=False):
        st.json(fusion)
    with st.expander("Run Metadata", expanded=False):
        st.json(metadata)
    with st.expander("Raw Result JSON", expanded=False):
        st.json(debug.get("raw_result") or {})
