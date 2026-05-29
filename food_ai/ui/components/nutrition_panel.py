from __future__ import annotations

import streamlit as st


def render_nutrition_panel(formatted: dict) -> None:
    nutrition = formatted.get("nutrition") or {}
    summary = nutrition.get("summary") or {}
    weighting = nutrition.get("weighting") or {}
    payload = nutrition.get("payload") or {}
    recalc = nutrition.get("recalc") or payload.get("nutrition_recalc") or {}

    st.subheader("USDA / Nutrition Bridge")
    columns = st.columns(4)
    columns[0].metric("Calories", summary.get("calories", "n/a"))
    columns[1].metric("Protein (g)", summary.get("protein_g", "n/a"))
    columns[2].metric("Fat (g)", summary.get("fat_g", "n/a"))
    columns[3].metric("Carbs (g)", summary.get("carbs_g", "n/a"))
    detail_columns = st.columns(3)
    detail_columns[0].metric("Fiber (g)", summary.get("fiber_g", "n/a"))
    detail_columns[1].metric("Sugars (g)", summary.get("total_sugars_g", "n/a"))
    detail_columns[2].metric("Sodium (mg)", summary.get("sodium_mg", "n/a"))

    st.info(
        payload.get("label")
        or "Estimated nutrition per 100g from dish ingredient priors. This is not a final portion-estimated plated nutrition result from the image."
    )
    if recalc.get("user_exclusions_applied"):
        excluded = ", ".join(recalc.get("excluded_ingredients") or [])
        st.warning(
            f"Nutrition recalculated from selected ingredients only. "
            f"Included: {recalc.get('included_count', 0)} | Excluded: {recalc.get('excluded_count', 0)}"
            + (f" | Excluded ingredients: {excluded}" if excluded else "")
        )
    st.caption(
        " | ".join(
            [
                f"requested weight source: {weighting.get('requested_weight_source') or 'recipenlg_frequency'}",
                f"actual weight source: {weighting.get('actual_weight_source') or 'recipenlg_frequency'}",
                f"USDA portion mass ingredients: {weighting.get('dish_mass_prior_applied_ingredient_count') or 0}",
                f"Nutrition5k matched dishes: {weighting.get('nutrition5k_matched_dish_count') or 0}",
            ]
        )
    )
    with st.expander("PALM-Ready Nutrition Payload", expanded=False):
        st.json(payload)
