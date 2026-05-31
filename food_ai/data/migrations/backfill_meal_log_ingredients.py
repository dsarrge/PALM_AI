from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
from typing import Any

from food_ai.config import resolve_runtime_db_path
from food_ai.food_bridge.ingredient_normalize import normalize_ingredient
from food_ai.ui.services.meal_log_service import ensure_meal_log_schema


NUTRIENT_KEYS = (
    "calories",
    "protein_g",
    "total_fat_g",
    "total_carbs_g",
    "fiber_g",
    "total_sugars_g",
    "added_sugars_g",
    "sodium_mg",
    "cholesterol_mg",
)


def _load_json(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if not value:
        return []
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _as_float(value: Any) -> float | None:
    if value in (None, "", "n/a"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ingredient_key(row_or_text: dict[str, Any] | str) -> str:
    if isinstance(row_or_text, str):
        value = row_or_text
    else:
        value = (
            row_or_text.get("normalized_ingredient")
            or row_or_text.get("normalized")
            or row_or_text.get("matched_ingredient")
            or row_or_text.get("ingredient")
            or ""
        )
    return normalize_ingredient(str(value)) or str(value).strip().lower()


def _contribution_value(row: dict[str, Any], key: str) -> float | None:
    aliases = {
        "calories": ("calories_contrib", "calories_contribution"),
        "protein_g": ("protein_g_contrib", "protein_contribution"),
        "total_fat_g": ("total_fat_g_contrib", "fat_contribution"),
        "total_carbs_g": ("total_carbs_g_contrib", "carbs_contribution"),
        "fiber_g": ("fiber_g_contrib",),
        "total_sugars_g": ("total_sugars_g_contrib",),
        "added_sugars_g": ("added_sugars_g_contrib",),
        "sodium_mg": ("sodium_mg_contrib",),
        "cholesterol_mg": ("cholesterol_mg_contrib",),
    }
    for candidate in aliases.get(key, (f"{key}_contrib",)):
        value = _as_float(row.get(candidate))
        if value is not None:
            return value
    return None


def _scale_ingredient_contributions(row: dict[str, Any], serving_weight_g: float, included: bool) -> dict[str, float | None]:
    multiplier = max(float(serving_weight_g), 0.0) / 100.0
    values: dict[str, float | None] = {}
    for key in NUTRIENT_KEYS:
        value = _contribution_value(row, key)
        if not included:
            values[key] = 0.0 if value is not None else None
        else:
            values[key] = round(value * multiplier, 3) if value is not None else None
    return values


def _row_included(row: dict[str, Any], excluded: set[str]) -> bool:
    key = _ingredient_key(row)
    if key and key in excluded:
        return False
    return bool(row.get("include_in_nutrition", row.get("included_in_nutrition", True)))


def _get_or_create_consumption_log(connection: sqlite3.Connection, meal_log: sqlite3.Row, total: dict[str, Any]) -> int:
    existing = connection.execute(
        """
        SELECT log_id
        FROM consumption_logs
        WHERE source = 'production_meal_logger'
          AND ref_id = ?
        ORDER BY log_id
        LIMIT 1
        """,
        (meal_log["meal_log_id"],),
    ).fetchone()
    consumed_at = meal_log["eaten_at"] or meal_log["created_at"]
    dish_key = normalize_ingredient(str(meal_log["dish_name"] or "")) or str(meal_log["dish_name"] or "").strip().lower()
    params = (
        dish_key,
        float(meal_log["serving_weight_g"] or 0.0),
        consumed_at,
        _as_float(total.get("calories") if isinstance(total, dict) else meal_log["calories"]),
        _as_float(total.get("protein_g") if isinstance(total, dict) else meal_log["protein_g"]),
        _as_float(total.get("total_fat_g") if isinstance(total, dict) else meal_log["total_fat_g"]),
        _as_float(total.get("total_carbs_g") if isinstance(total, dict) else meal_log["total_carbs_g"]),
        _as_float(total.get("fiber_g") if isinstance(total, dict) else meal_log["fiber_g"]),
        _as_float(total.get("total_sugars_g") if isinstance(total, dict) else meal_log["total_sugars_g"]),
        _as_float(total.get("added_sugars_g") if isinstance(total, dict) else meal_log["added_sugars_g"]),
        _as_float(total.get("sodium_mg") if isinstance(total, dict) else meal_log["sodium_mg"]),
        _as_float(total.get("cholesterol_mg") if isinstance(total, dict) else meal_log["cholesterol_mg"]),
        _as_float(meal_log["confidence"]),
    )
    if existing:
        log_id = int(existing["log_id"])
        connection.execute(
            """
            UPDATE consumption_logs
            SET dish_key = ?,
                grams_consumed = ?,
                consumed_at = ?,
                calories = ?,
                protein_g = ?,
                total_fat_g = ?,
                total_carbs_g = ?,
                fiber_g = ?,
                total_sugars_g = ?,
                added_sugars_g = ?,
                sodium_mg = ?,
                cholesterol_mg = ?,
                confidence = ?
            WHERE log_id = ?
            """,
            (*params, log_id),
        )
        return log_id
    cursor = connection.execute(
        """
        INSERT INTO consumption_logs (
            source,
            ref_id,
            dish_key,
            grams_consumed,
            consumed_at,
            calories,
            protein_g,
            total_fat_g,
            total_carbs_g,
            fiber_g,
            total_sugars_g,
            added_sugars_g,
            sodium_mg,
            cholesterol_mg,
            confidence,
            flags
        )
        VALUES ('production_meal_logger', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (meal_log["meal_log_id"], *params),
    )
    return int(cursor.lastrowid)


def _insert_ingredient_row(
    connection: sqlite3.Connection,
    *,
    log_id: int,
    row: dict[str, Any],
    serving_weight_g: float,
    included: bool,
) -> None:
    grams_per_100g = _as_float(row.get("grams_per_100g_dish") or row.get("grams_default")) or 0.0
    grams_in_meal = round(grams_per_100g * max(float(serving_weight_g), 0.0) / 100.0, 3)
    nutrients = _scale_ingredient_contributions(row, serving_weight_g, included)
    connection.execute(
        """
        INSERT INTO meal_log_ingredients (
            log_id,
            canonical_food_id,
            ingredient_name,
            matched_ingredient,
            included_in_nutrition,
            grams_in_meal,
            grams_per_100g_dish,
            calories,
            protein_g,
            total_fat_g,
            total_carbs_g,
            fiber_g,
            total_sugars_g,
            added_sugars_g,
            sodium_mg,
            cholesterol_mg,
            source_fdc_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            log_id,
            None,
            str(row.get("ingredient") or row.get("normalized_ingredient") or "Unknown ingredient"),
            row.get("matched_ingredient"),
            1 if included else 0,
            grams_in_meal,
            grams_per_100g,
            nutrients.get("calories"),
            nutrients.get("protein_g"),
            nutrients.get("total_fat_g"),
            nutrients.get("total_carbs_g"),
            nutrients.get("fiber_g"),
            nutrients.get("total_sugars_g"),
            nutrients.get("added_sugars_g"),
            nutrients.get("sodium_mg"),
            nutrients.get("cholesterol_mg"),
            row.get("matched_fdc_id") or row.get("fdc_id"),
        ),
    )


def backfill_meal_log_ingredients(db_path: str | Path | None = None) -> dict[str, int | str]:
    database_path = resolve_runtime_db_path(db_path)
    if not database_path.exists():
        raise FileNotFoundError(database_path)
    with sqlite3.connect(database_path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        ensure_meal_log_schema(connection)
        meal_logs = connection.execute(
            """
            SELECT
                meal_log_id,
                created_at,
                eaten_at,
                dish_name,
                confidence,
                serving_weight_g,
                calories,
                protein_g,
                total_fat_g,
                total_carbs_g,
                fiber_g,
                total_sugars_g,
                added_sugars_g,
                sodium_mg,
                cholesterol_mg,
                nutrition_total_json,
                included_ingredients_json,
                excluded_ingredients_json
            FROM meal_logs
            ORDER BY datetime(created_at)
            """
        ).fetchall()
        ingredient_rows_inserted = 0
        logs_processed = 0
        for meal_log in meal_logs:
            total = _load_json(meal_log["nutrition_total_json"])
            included_rows = _load_json(meal_log["included_ingredients_json"])
            excluded_rows = _load_json(meal_log["excluded_ingredients_json"])
            if not isinstance(included_rows, list):
                included_rows = []
            if not isinstance(excluded_rows, list):
                excluded_rows = []
            excluded_keys = {_ingredient_key(row) for row in excluded_rows if isinstance(row, dict)}
            log_id = _get_or_create_consumption_log(connection, meal_log, total if isinstance(total, dict) else {})
            connection.execute("DELETE FROM meal_log_ingredients WHERE log_id = ?", (log_id,))
            for row in included_rows:
                if isinstance(row, dict):
                    _insert_ingredient_row(
                        connection,
                        log_id=log_id,
                        row=row,
                        serving_weight_g=float(meal_log["serving_weight_g"] or 0.0),
                        included=_row_included(row, excluded_keys),
                    )
                    ingredient_rows_inserted += 1
            for row in excluded_rows:
                if isinstance(row, dict):
                    _insert_ingredient_row(
                        connection,
                        log_id=log_id,
                        row=row,
                        serving_weight_g=float(meal_log["serving_weight_g"] or 0.0),
                        included=False,
                    )
                    ingredient_rows_inserted += 1
            logs_processed += 1
        connection.commit()
    return {
        "db_path": str(database_path),
        "meal_logs_processed": logs_processed,
        "meal_log_ingredient_rows_inserted": ingredient_rows_inserted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill consumption_logs and meal_log_ingredients from existing meal_logs JSON payloads.")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    print(backfill_meal_log_ingredients(args.db))


if __name__ == "__main__":
    main()
