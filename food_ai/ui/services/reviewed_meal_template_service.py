from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
from typing import Any

from food_ai.config import resolve_runtime_db_path
from food_ai.food_bridge.ingredient_normalize import normalize_ingredient
from food_ai.ui.services.meal_log_service import ensure_meal_log_schema


LOOKBACK_LIMIT = 250


def _safe_json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return None
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _dish_key(value: Any) -> str:
    text = str(value or "").replace("_", " ").strip().lower()
    return normalize_ingredient(text) or text


def _top_dish_name(result: dict[str, Any]) -> str:
    candidates = result.get("dish_fused_topk") or result.get("dish_classifier_topk") or []
    if not candidates:
        return ""
    top = candidates[0] or {}
    return str(top.get("name") or top.get("label") or "").replace("_", " ").strip()


def _find_latest_template_row(connection: sqlite3.Connection, dish_name: str) -> dict[str, Any] | None:
    target_key = _dish_key(dish_name)
    if not target_key:
        return None
    rows = connection.execute(
        """
        SELECT
            meal_log_id,
            created_at,
            eaten_at,
            dish_name,
            user_label,
            serving_weight_g,
            calories,
            nutrition_per_100g_json,
            nutrition_total_json,
            included_ingredients_json,
            excluded_ingredients_json,
            inference_summary_json,
            inference_result_json
        FROM meal_logs
        ORDER BY datetime(created_at) DESC
        LIMIT ?
        """,
        (LOOKBACK_LIMIT,),
    ).fetchall()
    for row in rows:
        payload = dict(row)
        names = (payload.get("dish_name"), payload.get("user_label"))
        if any(_dish_key(name) == target_key for name in names if name):
            return payload
    return None


def _force_saved_grams(row: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    grams = updated.get("grams_per_100g_dish") or updated.get("grams_default") or updated.get("adjusted_grams_after_exclusions")
    if grams is not None:
        updated["grams_per_100g_dish"] = grams
        updated["grams_default"] = grams
        updated["grams_mean"] = grams
        updated["grams_mode"] = grams
        updated["mean_grams_per_100g_dish"] = grams
        updated["mode_grams_per_100g_dish"] = grams
    updated["reviewed_meal_template_applied"] = True
    updated["weight_source"] = "reviewed_meal_template"
    if not updated.get("portion_method"):
        updated["portion_method"] = "reviewed_meal_template"
    return updated


def _template_rows(saved_result: dict[str, Any], included_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ranked = saved_result.get("ingredients_ranked") or []
    visible = saved_result.get("visible_ingredients") or ranked
    if ranked:
        ranked_out = [_force_saved_grams(row) for row in ranked if isinstance(row, dict)]
        visible_out = [_force_saved_grams(row) for row in visible if isinstance(row, dict)]
        return ranked_out, visible_out

    fallback = [_force_saved_grams(row) for row in included_rows if isinstance(row, dict)]
    return fallback, fallback


def _overlay_template_result(current_result: dict[str, Any], template_row: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(current_result)
    saved_result = _safe_json_loads(template_row.get("inference_result_json")) or {}
    included_rows = _safe_json_loads(template_row.get("included_ingredients_json")) or []
    excluded_rows = _safe_json_loads(template_row.get("excluded_ingredients_json")) or []
    saved_palm_ready = copy.deepcopy(saved_result.get("palm_ready") or {})
    saved_normalization = copy.deepcopy(saved_result.get("ingredient_normalization") or {})

    ranked, visible = _template_rows(saved_result, included_rows)
    if ranked:
        result["ingredients_ranked"] = ranked
    if visible:
        result["visible_ingredients"] = visible
    if saved_normalization:
        result["ingredient_normalization"] = saved_normalization

    palm_ready = dict(saved_palm_ready or result.get("palm_ready") or {})
    for key in ("matched_ingredients", "unmatched_ingredients"):
        rows = palm_ready.get(key) or []
        if isinstance(rows, list):
            palm_ready[key] = [_force_saved_grams(row) for row in rows if isinstance(row, dict)]
    palm_ready["reviewed_meal_template"] = {
        "applied": True,
        "meal_log_id": template_row.get("meal_log_id"),
        "dish_name": template_row.get("dish_name"),
        "user_label": template_row.get("user_label"),
        "created_at": template_row.get("created_at"),
        "serving_weight_g": template_row.get("serving_weight_g"),
        "source": "meal_logs",
    }
    result["palm_ready"] = palm_ready
    result["reviewed_meal_template"] = palm_ready["reviewed_meal_template"]

    metadata = dict(result.get("metadata") or {})
    metadata["reviewed_meal_template_applied"] = True
    metadata["reviewed_meal_template_meal_log_id"] = template_row.get("meal_log_id")
    result["metadata"] = metadata

    warnings = list(result.get("warnings") or [])
    warnings.append("reviewed_meal_template_applied")
    result["warnings"] = list(dict.fromkeys(warnings))
    result["saved_template_excluded_ingredients"] = excluded_rows
    return result


def apply_reviewed_meal_template(
    inference_result: dict[str, Any],
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    dish_name = _top_dish_name(inference_result)
    database_path = resolve_runtime_db_path(db_path)
    if not dish_name or not database_path.exists():
        return inference_result
    with sqlite3.connect(database_path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        ensure_meal_log_schema(connection)
        template_row = _find_latest_template_row(connection, dish_name)
    if template_row is None:
        return inference_result
    return _overlay_template_result(inference_result, template_row)


__all__ = ["apply_reviewed_meal_template"]
