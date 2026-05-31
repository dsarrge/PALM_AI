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


def _load_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return None
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _key(value: Any) -> str:
    text = str(value or "").replace("_", " ").strip().lower()
    return normalize_ingredient(text) or text


def _ingredient_key(row: dict[str, Any]) -> str:
    return _key(row.get("normalized_ingredient") or row.get("normalized") or row.get("ingredient") or row.get("matched_ingredient"))


def _top_dish_name(result: dict[str, Any]) -> str:
    candidates = result.get("dish_fused_topk") or result.get("dish_classifier_topk") or []
    if not candidates:
        return ""
    top = candidates[0] or {}
    return str(top.get("name") or top.get("label") or "").replace("_", " ").strip()


def _latest_log_for_dish(connection: sqlite3.Connection, dish_name: str) -> dict[str, Any] | None:
    target = _key(dish_name)
    if not target:
        return None
    rows = connection.execute(
        """
        SELECT
            meal_log_id,
            created_at,
            dish_name,
            user_label,
            serving_weight_g,
            nutrition_per_100g_json,
            nutrition_total_json,
            included_ingredients_json,
            excluded_ingredients_json,
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
        if any(_key(name) == target for name in names if name):
            return payload
    return None


def _saved_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    grams = item.get("grams_per_100g_dish") or item.get("grams_default") or item.get("adjusted_grams_after_exclusions")
    if grams is not None:
        item["grams_per_100g_dish"] = grams
        item["grams_default"] = grams
        item["grams_mean"] = grams
        item["grams_mode"] = grams
        item["mean_grams_per_100g_dish"] = grams
        item["mode_grams_per_100g_dish"] = grams
    included = bool(item.get("included_in_nutrition", item.get("include_in_nutrition", True)))
    item["included_in_nutrition"] = included
    item["include_in_nutrition"] = included
    item["reviewed_meal_template_applied"] = True
    item["reviewed_meal_template_source"] = "saved_log"
    item["weight_source"] = "reviewed_meal_template"
    item["portion_method"] = item.get("portion_method") or "reviewed_meal_template"
    return item


def _extra_display_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["included_in_nutrition"] = False
    item["include_in_nutrition"] = False
    item["reviewed_meal_template_applied"] = True
    item["reviewed_meal_template_source"] = "current_inference_display_only"
    item["exclusion_reason"] = "latest_saved_log_is_default"
    return item


def _saved_display_rows(saved_result: dict[str, Any], included_rows: list[dict[str, Any]], excluded_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = saved_result.get("ingredients_ranked") or (included_rows + excluded_rows)
    return [_saved_row(row) for row in rows if isinstance(row, dict)]


def _display_rows(saved_rows: list[dict[str, Any]], current_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = list(saved_rows)
    seen = {_ingredient_key(row) for row in rows if _ingredient_key(row)}
    for row in current_rows:
        if not isinstance(row, dict):
            continue
        key = _ingredient_key(row)
        if key and key in seen:
            continue
        rows.append(_extra_display_row(row))
        if key:
            seen.add(key)
    return rows


def _saved_palm_ready(saved_result: dict[str, Any], current_result: dict[str, Any]) -> dict[str, Any]:
    palm_ready = copy.deepcopy(saved_result.get("palm_ready") or current_result.get("palm_ready") or {})
    for key in ("matched_ingredients", "unmatched_ingredients"):
        rows = palm_ready.get(key) or []
        if isinstance(rows, list):
            palm_ready[key] = [_saved_row(row) for row in rows if isinstance(row, dict)]
    return palm_ready


def _apply_log_template(current_result: dict[str, Any], log_row: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(current_result)
    saved_result = _load_json(log_row.get("inference_result_json")) or {}
    included_rows = _load_json(log_row.get("included_ingredients_json")) or []
    excluded_rows = _load_json(log_row.get("excluded_ingredients_json")) or []

    saved_rows = _saved_display_rows(saved_result, included_rows, excluded_rows)
    current_ranked = [row for row in (current_result.get("ingredients_ranked") or []) if isinstance(row, dict)]
    current_visible = [row for row in (current_result.get("visible_ingredients") or current_ranked) if isinstance(row, dict)]
    result["ingredients_ranked"] = _display_rows(saved_rows, current_ranked)
    result["visible_ingredients"] = _display_rows(saved_rows, current_visible)

    if saved_result.get("ingredient_normalization"):
        result["ingredient_normalization"] = copy.deepcopy(saved_result["ingredient_normalization"])

    palm_ready = _saved_palm_ready(saved_result, current_result)
    palm_ready["reviewed_meal_template"] = {
        "applied": True,
        "meal_log_id": log_row.get("meal_log_id"),
        "dish_name": log_row.get("dish_name"),
        "user_label": log_row.get("user_label"),
        "created_at": log_row.get("created_at"),
        "serving_weight_g": log_row.get("serving_weight_g"),
        "source": "latest_meal_log",
        "current_inference_extras_are_display_only": True,
    }
    result["palm_ready"] = palm_ready
    result["reviewed_meal_template"] = palm_ready["reviewed_meal_template"]

    metadata = dict(result.get("metadata") or {})
    metadata["reviewed_meal_template_applied"] = True
    metadata["reviewed_meal_template_meal_log_id"] = log_row.get("meal_log_id")
    metadata["current_inference_extras_are_display_only"] = True
    result["metadata"] = metadata

    warnings = list(result.get("warnings") or [])
    warnings.extend(["reviewed_meal_template_applied", "current_inference_extras_display_only"])
    result["warnings"] = list(dict.fromkeys(warnings))
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
        log_row = _latest_log_for_dish(connection, dish_name)
    if log_row is None:
        return inference_result
    return _apply_log_template(inference_result, log_row)


__all__ = ["apply_reviewed_meal_template"]
