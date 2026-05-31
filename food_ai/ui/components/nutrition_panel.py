from __future__ import annotations

import streamlit as st

from food_ai.ui.services.nutrition_extra_service import added_sugars_from_formatted


def _display(value: object) -> object:
    return "n/a" if value is None else value


def render_nutrition_panel(formatted: dict) -> None:
    nutrition = formatted.get("nutrition") or {}
    summary = nutrition.get("summary") or {}
    weighting = nutrition.get("weighting") or {}
    payload = nutrition.get("payload") or {}
    recalc = nutrition.get("recalc") or payload.get("nutrition_recalc") or {}
    added_sugars_g = added_sugars_from_formatted(formatted)

    st.subheader("USDA / Nutrition Bridge")
    columns = st.columns(4)
    columns[0].metric("Calories", _display(summary.get("calories")))
    columns[1].metric("Protein (g)", _display(summary.get("protein_g")))
    columns[2].metric("Fat (g)", _display(summary.get("fat_g")))
    columns[3].metric("Carbs (g)", _display(summary.get("carbs_g")))

    detail_columns = st.columns(4)
    detail_columns[0].metric("Fiber (g)", _display(summary.get("fiber_g")))
    detail_columns[1].metric("Total Sugars (g)", _display(summary.get("total_sugars_g")))
    detail_columns[2].metric("Added Sugars (g)", _display(added_sugars_g))
    detail_columns[3].metric("Sodium (mg)", _display(summary.get("sodium_mg")))

    st.caption("Sugar values are grams per 100g dish. Sodium is milligrams per 100g dish. Per-serving save scales both by serving_weight_g / 100.")

    st.info(
        payload.get("label")
        or "Estimated nutrition per 100g from dish ingredient priors. This is not a final portion-estimated plated nutrition result from the image."
    )
    if added_sugars_g is not None:
        st.caption("Added sugars are shown only when available from source columns or safely inferred for sugar-like ingredients; they are not added into total sugars.")
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
        payload_with_optional = dict(payload)
        if added_sugars_g is not None:
            estimated = dict(payload_with_optional.get("estimated_nutrition_per_100g") or {})
            estimated["added_sugars_g"] = added_sugars_g
            payload_with_optional["estimated_nutrition_per_100g"] = estimated
        st.json(payload_with_optional)
