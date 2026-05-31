from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from food_ai.ui.services.manual_ingredient_service import load_ingredient_suggestions


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
    "remove",
]


def _ingredient_key(row: dict) -> str:
    return str(row.get("normalized_ingredient") or row.get("ingredient") or "").strip().lower()


def _manual_key(row: dict[str, Any]) -> str:
    return str(row.get("ingredient") or "").strip().lower()


def _render_table(title: str, rows: list[dict]) -> None:
    st.markdown(f"**{title}**")
    if not rows:
        st.info(f"No {title.lower()} available.")
        return
    st.dataframe(rows, use_container_width=True)


def _clean_manual_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
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
        key = ingredient.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(
            {
                "include_in_nutrition": bool(row.get("include_in_nutrition", True)),
                "ingredient": ingredient,
                "grams_per_100g_dish": round(grams_float, 3),
            }
        )
    return cleaned


def _add_manual_ingredient(ingredient: str, grams_per_100g_dish: float) -> None:
    ingredient_text = str(ingredient or "").strip()
    if not ingredient_text:
        st.warning("Choose or enter an ingredient before adding it.")
        return
    if grams_per_100g_dish <= 0.0:
        st.warning("Enter grams per 100g dish before adding the ingredient.")
        return

    rows = _clean_manual_rows(st.session_state.get("manual_ingredients") or [])
    new_row = {
        "include_in_nutrition": True,
        "ingredient": ingredient_text,
        "grams_per_100g_dish": round(float(grams_per_100g_dish), 3),
    }
    existing_index = next((index for index, row in enumerate(rows) if _manual_key(row) == ingredient_text.lower()), None)
    if existing_index is None:
        rows.append(new_row)
    else:
        rows[existing_index] = new_row
    st.session_state["manual_ingredients"] = rows
    st.session_state["manual_ingredient_notice"] = f"Added {ingredient_text}."


def _render_manual_ingredient_editor(db_path: str | Path | None = None) -> None:
    st.markdown("**Add Missing Ingredients**")
    st.caption(
        "Start typing to filter existing PALM ingredients, choose the closest match, enter grams per 100g dish, then add it. "
        "Use custom text only when the ingredient is not in the suggestions."
    )
    notice = st.session_state.get("manual_ingredient_notice")
    if notice:
        st.success(str(notice))
        st.session_state["manual_ingredient_notice"] = None

    query_col, select_col, grams_col, add_col = st.columns([1.3, 1.7, 0.8, 0.7], gap="small")
    with query_col:
        query = st.text_input(
            "Search ingredient",
            key="manual_ingredient_query",
            placeholder="type: ricotta, oil, pasta...",
        )
    suggestions = load_ingredient_suggestions(query, db_path=db_path, limit=40)
    options = [""] + suggestions
    with select_col:
        selected = st.selectbox(
            "Suggested match",
            options=options,
            key="manual_ingredient_selected",
            format_func=lambda value: "Select suggested ingredient" if value == "" else value,
        )
    with grams_col:
        grams = st.number_input(
            "g / 100g",
            min_value=0.0,
            max_value=100.0,
            value=10.0,
            step=1.0,
            key="manual_ingredient_grams",
        )
    with add_col:
        st.write("")
        st.write("")
        if st.button("Add", width="stretch", key="add_manual_ingredient_button"):
            _add_manual_ingredient(selected or query, grams)
            st.rerun()

    custom = st.text_input(
        "Custom ingredient fallback",
        key="manual_ingredient_custom",
        placeholder="Only use this if the ingredient is not suggested above.",
    )
    if custom and st.button("Add Custom Ingredient", width="stretch", key="add_custom_manual_ingredient_button"):
        _add_manual_ingredient(custom, float(st.session_state.get("manual_ingredient_grams") or 0.0))
        st.rerun()

    manual_rows = _clean_manual_rows(st.session_state.get("manual_ingredients") or [])
    if not manual_rows:
        st.info("No manually added ingredients yet.")
        return

    table_rows = [{**row, "remove": False} for row in manual_rows]
    edited_manual_rows = st.data_editor(
        table_rows,
        key=f"manual_ingredient_editor_{st.session_state.get('last_inference_id') or 'current'}",
        use_container_width=True,
        hide_index=True,
        height=min(300, 40 + (len(table_rows) + 1) * 35),
        column_order=MANUAL_INGREDIENT_TABLE_COLUMNS,
        disabled=["ingredient"],
        column_config={
            "include_in_nutrition": st.column_config.CheckboxColumn("Include", default=True),
            "ingredient": st.column_config.TextColumn("Ingredient"),
            "grams_per_100g_dish": st.column_config.NumberColumn(
                "Grams / 100g Dish",
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                format="%.1f",
            ),
            "remove": st.column_config.CheckboxColumn("Remove", default=False),
        },
    )
    if hasattr(edited_manual_rows, "to_dict"):
        edited_manual_rows = edited_manual_rows.to_dict("records")
    st.session_state["manual_ingredients"] = _clean_manual_rows(
        [row for row in edited_manual_rows if not row.get("remove")]
    )


def render_ingredients_panel(
    formatted: dict,
    *,
    allow_manual_ingredients: bool = False,
    db_path: str | Path | None = None,
) -> None:
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
        _render_manual_ingredient_editor(db_path=db_path)

    hidden = ingredients.get("hidden") or []
    if hidden:
        st.caption("Hidden ingredients are preserved by recipe priors and heuristics even when they are not visibly obvious.")
        _render_table("Hidden Ingredients Preserved", hidden)
