from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from food_ai.config import resolve_runtime_db_path


NUTRIENT_KEYS = (
    "calories",
    "protein_g",
    "total_fat_g",
    "total_carbs_g",
    "fiber_g",
    "total_sugars_g",
    "sodium_mg",
    "cholesterol_mg",
)


@dataclass(frozen=True, slots=True)
class MealLogRecord:
    meal_log_id: str
    db_path: Path
    dish_name: str
    serving_weight_g: float
    calories: float | None
    created_at: str


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _as_float(value: Any) -> float | None:
    if value in (None, "", "n/a"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.endswith("%"):
            parsed = _as_float(stripped[:-1])
            return parsed / 100.0 if parsed is not None else None
        return _as_float(stripped)
    return _as_float(value)


def _nutrition_value(per_100g: dict[str, Any], key: str) -> float | None:
    aliases = {
        "total_fat_g": ("total_fat_g", "fat_g"),
        "total_carbs_g": ("total_carbs_g", "carbs_g", "carbohydrates_g"),
    }
    for candidate in aliases.get(key, (key,)):
        value = _as_float(per_100g.get(candidate))
        if value is not None:
            return value
    return None


def _scale_nutrition(per_100g: dict[str, Any], serving_weight_g: float) -> dict[str, float | None]:
    multiplier = max(float(serving_weight_g), 0.0) / 100.0
    scaled: dict[str, float | None] = {}
    for key in NUTRIENT_KEYS:
        value = _nutrition_value(per_100g, key)
        scaled[key] = round(value * multiplier, 3) if value is not None else None
    return scaled


def _ingredient_name(row: dict[str, Any]) -> str:
    return str(row.get("normalized_ingredient") or row.get("ingredient") or "").strip().lower()


def _selected_ingredients(
    formatted: dict[str, Any],
    excluded_ingredients: list[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    excluded = {str(item).strip().lower() for item in (excluded_ingredients or []) if str(item).strip()}
    ranked = ((formatted.get("ingredients") or {}).get("ranked") or [])
    included_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    for row in ranked:
        item = dict(row)
        key = _ingredient_name(item)
        included = bool(item.get("include_in_nutrition", True)) and key not in excluded
        item["include_in_nutrition"] = included
        if included:
            included_rows.append(item)
        else:
            excluded_rows.append(item)
    return included_rows, excluded_rows


def ensure_meal_log_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS meal_logs (
            meal_log_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            eaten_at TEXT,
            image_path TEXT,
            dish_name TEXT NOT NULL,
            user_label TEXT,
            confidence REAL,
            confidence_label TEXT,
            serving_weight_g REAL NOT NULL,
            calories REAL,
            protein_g REAL,
            total_fat_g REAL,
            total_carbs_g REAL,
            fiber_g REAL,
            total_sugars_g REAL,
            sodium_mg REAL,
            cholesterol_mg REAL,
            nutrition_per_100g_json TEXT NOT NULL,
            nutrition_total_json TEXT NOT NULL,
            included_ingredients_json TEXT NOT NULL,
            excluded_ingredients_json TEXT NOT NULL,
            notes TEXT,
            inference_summary_json TEXT NOT NULL,
            inference_result_json TEXT NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_meal_logs_created_at ON meal_logs(created_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_meal_logs_eaten_at ON meal_logs(eaten_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_meal_logs_dish_name ON meal_logs(dish_name)")


def save_meal_log(
    *,
    image_path: str | Path | None,
    inference_result: dict[str, Any],
    formatted_result: dict[str, Any],
    serving_weight_g: float = 100.0,
    notes: str = "",
    user_label: str = "",
    eaten_at: str | None = None,
    excluded_ingredients: list[str] | None = None,
    db_path: str | Path | None = None,
) -> MealLogRecord:
    resolved_db_path = resolve_runtime_db_path(db_path)
    resolved_db_path.parent.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(timezone.utc).isoformat()
    meal_log_id = uuid4().hex

    summary = formatted_result.get("summary") or {}
    nutrition = formatted_result.get("nutrition") or {}
    per_100g = dict(nutrition.get("summary") or {})
    total = _scale_nutrition(per_100g, serving_weight_g)
    included_rows, excluded_rows = _selected_ingredients(formatted_result, excluded_ingredients)

    dish_name = str(user_label or summary.get("top_dish") or "Unlabeled meal").strip() or "Unlabeled meal"
    confidence_label = str(summary.get("overall_confidence") or "")
    confidence = _parse_confidence(confidence_label)

    with sqlite3.connect(resolved_db_path, timeout=30) as connection:
        connection.execute("PRAGMA busy_timeout = 30000")
        ensure_meal_log_schema(connection)
        connection.execute(
            """
            INSERT INTO meal_logs (
                meal_log_id,
                created_at,
                eaten_at,
                image_path,
                dish_name,
                user_label,
                confidence,
                confidence_label,
                serving_weight_g,
                calories,
                protein_g,
                total_fat_g,
                total_carbs_g,
                fiber_g,
                total_sugars_g,
                sodium_mg,
                cholesterol_mg,
                nutrition_per_100g_json,
                nutrition_total_json,
                included_ingredients_json,
                excluded_ingredients_json,
                notes,
                inference_summary_json,
                inference_result_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meal_log_id,
                created_at,
                eaten_at,
                str(image_path) if image_path else None,
                dish_name,
                user_label.strip() or None,
                confidence,
                confidence_label or None,
                float(serving_weight_g),
                total.get("calories"),
                total.get("protein_g"),
                total.get("total_fat_g"),
                total.get("total_carbs_g"),
                total.get("fiber_g"),
                total.get("total_sugars_g"),
                total.get("sodium_mg"),
                total.get("cholesterol_mg"),
                _json_dumps(per_100g),
                _json_dumps(total),
                _json_dumps(included_rows),
                _json_dumps(excluded_rows),
                notes.strip() or None,
                _json_dumps(summary),
                _json_dumps(inference_result),
            ),
        )
        connection.commit()

    return MealLogRecord(
        meal_log_id=meal_log_id,
        db_path=resolved_db_path,
        dish_name=dish_name,
        serving_weight_g=float(serving_weight_g),
        calories=total.get("calories"),
        created_at=created_at,
    )


def list_recent_meal_logs(
    *,
    db_path: str | Path | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    resolved_db_path = resolve_runtime_db_path(db_path)
    if not resolved_db_path.exists():
        return []

    with sqlite3.connect(resolved_db_path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        ensure_meal_log_schema(connection)
        rows = connection.execute(
            """
            SELECT
                meal_log_id,
                created_at,
                eaten_at,
                dish_name,
                confidence_label,
                serving_weight_g,
                calories,
                protein_g,
                total_fat_g,
                total_carbs_g,
                sodium_mg,
                notes
            FROM meal_logs
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(row) for row in rows]


__all__ = ["MealLogRecord", "ensure_meal_log_schema", "list_recent_meal_logs", "save_meal_log"]
