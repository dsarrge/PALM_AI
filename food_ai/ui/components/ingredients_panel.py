from __future__ import annotations

from typing import Any

import streamlit as st


INGREDIENT_TABLE_COLUMNS = [
    "include_in_nutrition",
    "ingredient",
    "matched_ingredient",
    "status",
    "weight",
    "grams_default",
    "grams_per_100g_dish",
    "portion_method",
    "weight_source",
    "source_count",
    "recipe_pct",
    "match_method",
    "reason",
    "calories_contrib",
    "protein_g_contrib",
    "total_fat_g_contrib",
    "total_carbs_g_contrib",
    "fiber_g_contrib",
    "total_sugars_g_contrib",
    "sodium_mg_contrib",
    "cholesterol_mg_contrib",
]

MANUAL_INGREDIENT_TABLE_COLUMNS = [
    "include_in_nutrition",
    "ingredient",
    "grams_per_100g_dish",
]


def _ingredient_key(row: dict) -> str:
    return str(row.get("normalized_ingredient") or row.get("ingredient") or "").strip().lower()


def _render_table(title: str, rows: list[dict]) -> None:
    st.markdown(f"**{title}**")
    if not rows:
        st.info(f"No {title.lower()} available.")
        return
    st.dataframe(rows, use_container_width=True)


def _clean_manual_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows or []:
        ingredient = str(row.get("ingredient") or "").strip()
        grams = row.get("grams_per_100g_dish")
        if not ingredient:
            continue
        try:
            grams_float = float(grams)
        except (TypeError, ValueError):
            continue
        if grams_float <= 0.0:
            continue
        cleaned.append(
            {
                "include_in_nutrition": bool(row.get("include_in_nutrition", True)),
                "ingredient": ingredient,
                "grams_per_100g_dish": round(grams_float, 3),
            }
        )
    return cleaned


def _render_manual_ingredient_editor() -> None:
    st.markdown("**Add Missing Ingredients**")
    st.caption(
        "Add ingredients the model missed. Use grams per 100g of the dish so PALM can scale them into the nutrition estimate. "
        "Press recalculation after editing."
    )
    manual_rows = st.session_state.get("manual_ingredients") or []
    edited_manual_rows = st.data_editor(
        manual_rows,
        key=f"manual_ingredient_editor_{st.session_state.get('last_inference_id') or 'current'}",
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_order=MANUAL_INGREDIENT_TABLE_COLUMNS,
        column_config={
            "include_in_nutrition": st.column_config.CheckboxColumn("Include", default=True),
            "ingredient": st.column_config.TextColumn("Ingredient", required=False),
            "grams_per_100g_dish": st.column_config.NumberColumn(
                "Grams / 100g Dish",
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                format="%.1f",
            ),
        },
    )
    if hasattr(edited_manual_rows, "to_dict"):
        edited_manual_rows = edited_manual_rows.to_dict("records")
    st.session_state["manual_ingredients"] = _clean_manual_rows(edited_manual_rows)


def render_ingredients_panel(formatted: dict, *, allow_manual_ingredients: bool = False) -> None:
    ingredients = formatted.get("ingredients") or {}
    st.subheader("Ingredient Predictions")
    ranked = ingredients.get("ranked") or []
    st.markdown("**Blended Ingredient Ranking**")
    if not ranked:
        st.info("No blended ingredient ranking available.")
    else:
        excluded = set(st.session_state.get("excluded_ingredients") or [])
        rows = []
        for row in ranked:
            item = dict(row)
            key = _ingredient_key(item)
            default_include = bool(item.get("include_in_nutrition", True))
            item["include_in_nutrition"] = default_include and key not in excluded
            rows.append(item)
        editor_key = f"ingredient_inclusion_editor_{st.session_state.get('last_inference_id') or 'current'}"
        edited_rows = st.data_editor(
            rows,
            key=editor_key,
            use_container_width=True,
            hide_index=True,
            height=min(520, 40 + (len(rows) + 1) * 35),
            column_order=INGREDIENT_TABLE_COLUMNS,
            disabled=[
                "ingredient",
                "matched_ingredient",
                "status",
                "weight",
                "grams_default",
                "grams_per_100g_dish",
                "portion_method",
                "weight_source",
                "source_count",
                "recipe_pct",
                "match_method",
                "reason",
                "calories_contrib",
                "protein_g_contrib",
                "total_fat_g_contrib",
                "total_carbs_g_contrib",
                "fiber_g_contrib",
                "total_sugars_g_contrib",
                "sodium_mg_contrib",
                "cholesterol_mg_contrib",
            ],
            column_config={
                "include_in_nutrition": st.column_config.CheckboxColumn("Include", default=True),
                "ingredient": "Ingredient",
                "matched_ingredient": "Matched Ingredient",
                "status": "Status",
                "weight": "Weight",
                "grams_default": "Default Grams",
                "grams_per_100g_dish": "Grams / 100g Dish",
                "portion_method": "Portion Method",
                "weight_source": "Weight Source",
                "source_count": "Source Count",
                "recipe_pct": "Recipe %",
                "match_method": "Match Method",
                "reason": "Reason / Warning",
                "calories_contrib": "Calories",
                "protein_g_contrib": "Protein (g)",
                "total_fat_g_contrib": "Fat (g)",
                "total_carbs_g_contrib": "Carbs (g)",
                "fiber_g_contrib": "Fiber (g)",
                "total_sugars_g_contrib": "Sugars (g)",
                "sodium_mg_contrib": "Sodium (mg)",
                "cholesterol_mg_contrib": "Cholesterol (mg)",
            },
        )
        if hasattr(edited_rows, "to_dict"):
            edited_rows = edited_rows.to_dict("records")
        st.session_state["excluded_ingredients"] = [
            _ingredient_key(row)
            for row in edited_rows
            if not row.get("include_in_nutrition") and _ingredient_key(row)
        ]

    if allow_manual_ingredients:
        _render_manual_ingredient_editor()

    hidden = ingredients.get("hidden") or []
    if hidden:
        st.caption("Hidden ingredients are preserved by recipe priors and heuristics even when they are not visibly obvious.")
        _render_table("Hidden Ingredients Preserved", hidden)
