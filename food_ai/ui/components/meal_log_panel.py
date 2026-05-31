from __future__ import annotations

import streamlit as st


def render_meal_log_form(default_name: str = "") -> dict[str, object]:
    st.subheader("Save Meal Log")
    with st.form("meal_log_form", clear_on_submit=False):
        user_label = st.text_input(
            "Meal name",
            value=default_name,
            help="Edit the dish name before saving if the classifier label needs a human correction.",
        )
        serving_weight_g = st.number_input(
            "Serving weight (grams)",
            min_value=1.0,
            max_value=5000.0,
            value=100.0,
            step=5.0,
            help="Nutrition is stored as the per-serving estimate by scaling the PALM per-100g estimate.",
        )
        eaten_at = st.text_input(
            "Meal timestamp",
            value="",
            help="Optional timestamp for when the meal was eaten.",
        )
        notes = st.text_area("Notes", value="", height=90)
        save_clicked = st.form_submit_button("Save Meal to Database", width="stretch")

    return {
        "user_label": user_label,
        "serving_weight_g": serving_weight_g,
        "eaten_at": eaten_at,
        "notes": notes,
        "save_clicked": save_clicked,
    }


def render_recent_meal_logs(rows: list[dict]) -> None:
    st.subheader("Recent Meal Logs")
    if not rows:
        st.info("No meal logs saved yet.")
        return

    display_rows = []
    for row in rows:
        display_rows.append(
            {
                "Created": row.get("created_at"),
                "Meal": row.get("dish_name"),
                "Weight (g)": row.get("serving_weight_g"),
                "Calories": row.get("calories"),
                "Protein (g)": row.get("protein_g"),
                "Carbs (g)": row.get("total_carbs_g"),
                "Fat (g)": row.get("total_fat_g"),
            }
        )
    st.dataframe(display_rows, use_container_width=True, hide_index=True)
