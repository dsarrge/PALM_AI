from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from food_ai.config import resolve_runtime_db_path
from food_ai.food_bridge.ingredient_normalize import normalize_ingredient


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
        per_100g_contrib = _contribution_value(row, key)
        if not included:
            values[key] = 0.0 if per_100g_contrib is not None else None
        else:
            values[key] = round(per_100g_contrib * multiplier, 3) if per_100g_contrib is not None else None
    return values


def _selected_ingredients(
    formatted: dict[str, Any],
    excluded_ingredients: list[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    excluded = {_ingredient_key(item) for item in (excluded_ingredients or []) if _ingredient_key(item)}
    ranked = ((formatted.get("ingredients") or {}).get("ranked") or [])
    included_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    for row in ranked:
        item = dict(row)
        key = _ingredient_key(item)
        included = bool(item.get("include_in_nutrition", True)) and not (key and key in excluded)
        item["include_in_nutrition"] = included
        item["included_in_nutrition"] = included
        if included:
            included_rows.append(item)
        else:
            excluded_rows.append(item)
    return included_rows, excluded_rows


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    if column not in _table_columns(connection, table):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


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
            added_sugars_g REAL,
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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS consumption_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            ref_id TEXT,
            canonical_food_id INTEGER,
            dish_key TEXT,
            product_id INTEGER,
            grams_consumed REAL NOT NULL,
            consumed_at TEXT NOT NULL,
            calories REAL,
            protein_g REAL,
            total_fat_g REAL,
            total_carbs_g REAL,
            fiber_g REAL,
            total_sugars_g REAL,
            added_sugars_g REAL,
            sodium_mg REAL,
            cholesterol_mg REAL,
            confidence REAL DEFAULT 1.0,
            flags TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS meal_log_ingredients (
            meal_log_ingredient_id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_id INTEGER NOT NULL,
            canonical_food_id INTEGER,
            ingredient_name TEXT NOT NULL,
            matched_ingredient TEXT,
            included_in_nutrition INTEGER DEFAULT 1,
            grams_in_meal REAL NOT NULL,
            grams_per_100g_dish REAL,
            calories REAL,
            protein_g REAL,
            total_fat_g REAL,
            total_carbs_g REAL,
            fiber_g REAL,
            total_sugars_g REAL,
            added_sugars_g REAL,
            sodium_mg REAL,
            cholesterol_mg REAL,
            source_fdc_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (log_id) REFERENCES consumption_logs(log_id)
        )
        """
    )
    for table in ("meal_logs", "consumption_logs", "meal_log_ingredients"):
        if table in {"meal_logs", "consumption_logs", "meal_log_ingredients"}:
            _ensure_column(connection, table, "added_sugars_g", "REAL")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_meal_logs_created_at ON meal_logs(created_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_meal_logs_eaten_at ON meal_logs(eaten_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_meal_logs_dish_name ON meal_logs(dish_name)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_consumption_logs_ref_id ON consumption_logs(ref_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_consumption_logs_consumed_at ON consumption_logs(consumed_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_meal_log_ingredients_log ON meal_log_ingredients(log_id)")


def _insert_consumption_log(
    connection: sqlite3.Connection,
    *,
    meal_log_id: str,
    dish_name: str,
    serving_weight_g: float,
    consumed_at: str,
    total: dict[str, float | None],
    confidence: float | None,
) -> int:
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "production_meal_logger",
            meal_log_id,
            normalize_ingredient(dish_name) or dish_name.strip().lower(),
            float(serving_weight_g),
            consumed_at,
            total.get("calories"),
            total.get("protein_g"),
            total.get("total_fat_g"),
            total.get("total_carbs_g"),
            total.get("fiber_g"),
            total.get("total_sugars_g"),
            total.get("added_sugars_g"),
            total.get("sodium_mg"),
            total.get("cholesterol_mg"),
            confidence,
            None,
        ),
    )
    return int(cursor.lastrowid)


def _insert_meal_log_ingredients(
    connection: sqlite3.Connection,
    *,
    log_id: int,
    serving_weight_g: float,
    included_rows: list[dict[str, Any]],
    excluded_rows: list[dict[str, Any]],
) -> None:
    rows = included_rows + excluded_rows
    for row in rows:
        included = bool(row.get("include_in_nutrition", row.get("included_in_nutrition", True)))
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
    consumed_at = eaten_at or created_at

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
                added_sugars_g,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                total.get("added_sugars_g"),
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
        log_id = _insert_consumption_log(
            connection,
            meal_log_id=meal_log_id,
            dish_name=dish_name,
            serving_weight_g=float(serving_weight_g),
            consumed_at=consumed_at,
            total=total,
            confidence=confidence,
        )
        _insert_meal_log_ingredients(
            connection,
            log_id=log_id,
            serving_weight_g=float(serving_weight_g),
            included_rows=included_rows,
            excluded_rows=excluded_rows,
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
                total_sugars_g,
                added_sugars_g,
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
