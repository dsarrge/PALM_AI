from __future__ import annotations

import streamlit as st


# def render_recipes_panel(formatted: dict) -> None:
#     st.subheader("Supporting Recipe Evidence")
#     recipes = formatted.get("recipes") or []
#     if not recipes:
#         st.info("No candidate recipes were returned.")
#         return
#     st.dataframe(recipes, use_container_width=True)
#     st.caption("Recipe candidates provide title-level evidence and support ingredient aggregation. They are not guaranteed to match the plated meal exactly.")
