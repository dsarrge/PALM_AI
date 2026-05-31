from __future__ import annotations

import copy
from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Any

from food_ai.config import resolve_runtime_db_path
from food_ai.food_bridge.ingredient_normalize import normalize_ingredient


ADDED_SUGAR_COLUMN_CANDIDATES = (
    "added_sugars_g",
    "added_sugar_g",
    "added_sugars",
    "added_sugar",
    "added_sugars_g_per_100g",
)

SUGAR_LIKE_TOKENS = (
    "sugar",
    "syrup",
    "honey",
    "molasses",
    "glaze",
    "icing",
    "frosting",
    "jam",
    "jelly",
)


def _as_float(value: Any) -> float | None:
    if value in (None, "", "n/a"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(connection, table):
        return set()
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _pick_column(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def _ingredient_text(record: dict[str, Any]) -> str:
    return str(record.get("matched_ingredient") or record.get("ingredient") or record.get("normalized") or "").strip()


def _is_sugar_like(record: dict[str, Any]) -> bool:
    text = normalize_ingredient(_ingredient_text(record)) or _ingredient_text(record).lower()
    return any(token in text for token in SUGAR_LIKE_TOKENS)


def _normalized_weight(record: dict[str, Any]) -> float:
    value = _as_float(record.get("normalized_weight_used"))
    if value is not None:
        return max(0.0, value)
    grams = _as_float(record.get("adjusted_grams_after_exclusions"))
    if grams is None:
        grams = _as_float(record.get("grams_per_100g_dish") or record.get("grams_default"))
    if grams is not None:
        return max(0.0, grams / 100.0)
    return max(0.0, _as_float(record.get("weight")) or 0.0)


def _direct_added_sugars(record: dict[str, Any]) -> tuple[float | None, str | None]:
    nutrition = record.get("nutrition_per_100g") or {}
    for key in ADDED_SUGAR_COLUMN_CANDIDATES:
        value = _as_float(nutrition.get(key) or record.get(key))
        if value is not None:
            return value, "direct_nutrition_payload"
    return None, None


def _lookup_added_sugars_in_db(record: dict[str, Any], db_path: str | Path | None) -> tuple[float | None, str | None]:
    database_path = resolve_runtime_db_path(db_path)
    if not database_path.exists():
        return None, None
    ingredient = _ingredient_text(record)
    if not ingredient:
        return None, None
    normalized = normalize_ingredient(ingredient) or ingredient.lower()
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        for table in ("generic_ingredient_nutrition", "ingredient_nutrients"):
            columns = _table_columns(connection, table)
            sugar_col = _pick_column(columns, ADDED_SUGAR_COLUMN_CANDIDATES)
            name_cols = [col for col in ("ingredient", "normalized_ingredient", "name", "description") if col in columns]
            if not sugar_col or not name_cols:
                continue
            for name_col in name_cols:
                row = connection.execute(
                    f"SELECT {sugar_col} AS added_sugars_g FROM {table} WHERE LOWER({name_col}) = LOWER(?) LIMIT 1",
                    (normalized,),
                ).fetchone()
                value = _as_float(row["added_sugars_g"] if row else None)
                if value is not None:
                    return value, f"{table}.{sugar_col}"
        if _table_exists(connection, "generic_foods") and _table_exists(connection, "generic_food_nutrition"):
            food_cols = _table_columns(connection, "generic_foods")
            nutrition_cols = _table_columns(connection, "generic_food_nutrition")
            sugar_col = _pick_column(nutrition_cols, ADDED_SUGAR_COLUMN_CANDIDATES)
            name_cols = [col for col in ("ingredient", "food_name", "name", "description") if col in food_cols]
            if sugar_col and name_cols:
                for name_col in name_cols:
                    row = connection.execute(
                        f"""
                        SELECT n.{sugar_col} AS added_sugars_g
                        FROM generic_food_nutrition n
                        JOIN generic_foods g ON n.food_id = g.food_id
                        WHERE LOWER(g.{name_col}) = LOWER(?)
                        LIMIT 1
                        """,
                        (normalized,),
                    ).fetchone()
                    value = _as_float(row["added_sugars_g"] if row else None)
                    if value is not None:
                        return value, f"generic_food_nutrition.{sugar_col}"
    return None, None


def _fallback_added_sugars(record: dict[str, Any]) -> tuple[float | None, str | None]:
    if not _is_sugar_like(record):
        return None, None
    total_sugars = _as_float((record.get("nutrition_per_100g") or {}).get("total_sugars_g"))
    if total_sugars is None:
        return None, None
    return total_sugars, "sugar_like_total_sugars_fallback"


def added_sugars_per_100g(record: dict[str, Any], db_path: str | Path | None = None) -> tuple[float | None, str | None]:
    value, source = _direct_added_sugars(record)
    if value is not None:
        return value, source
    value, source = _lookup_added_sugars_in_db(record, db_path)
    if value is not None:
        return value, source
    return _fallback_added_sugars(record)


def enrich_added_sugars(result: dict[str, Any]) -> dict[str, Any]:
    enriched = copy.deepcopy(result)
    palm_ready = dict(enriched.get("palm_ready") or {})
    records = list(palm_ready.get("matched_ingredients") or [])
    db_path = (enriched.get("metadata") or {}).get("db_path")
    total = 0.0
    audits: list[dict[str, Any]] = []
    any_value = False
    for record in records:
        if not record.get("included_in_nutrition", True):
            continue
        per_100g, source = added_sugars_per_100g(record, db_path=db_path)
        if per_100g is None:
            continue
        weight = _normalized_weight(record)
        contribution = round(per_100g * weight, 3)
        any_value = True
        total += contribution
        nutrition = dict(record.get("nutrition_per_100g") or {})
        nutrition["added_sugars_g"] = per_100g
        record["nutrition_per_100g"] = nutrition
        contributions = dict(record.get("nutrient_contributions") or {})
        contributions["added_sugars_g_contrib"] = contribution
        record["nutrient_contributions"] = contributions
        audits.append(
            {
                "ingredient": _ingredient_text(record),
                "added_sugars_g_per_100g": per_100g,
                "normalized_weight_used": weight,
                "added_sugars_g_contrib": contribution,
                "source": source,
            }
        )
    if not any_value:
        return enriched
    palm_ready["matched_ingredients"] = records
    estimated = dict(palm_ready.get("estimated_nutrition_per_100g") or {})
    estimated["added_sugars_g"] = round(total, 3)
    palm_ready["estimated_nutrition_per_100g"] = estimated
    palm_ready["optional_nutrients"] = {
        **dict(palm_ready.get("optional_nutrients") or {}),
        "added_sugars_g": {
            "value_per_100g": round(total, 3),
            "note": "Added sugars are included only when source data has them or when a sugar-like ingredient safely falls back to total sugars.",
            "audits": audits,
        },
    }
    enriched["palm_ready"] = palm_ready
    warnings = list(enriched.get("warnings") or [])
    warnings.append("optional_added_sugars_estimated")
    enriched["warnings"] = list(dict.fromkeys(warnings))
    return enriched


def added_sugars_from_formatted(formatted: dict[str, Any]) -> float | None:
    raw_result = ((formatted.get("debug") or {}).get("raw_result") or {})
    enriched = enrich_added_sugars(raw_result) if raw_result else {}
    value = ((enriched.get("palm_ready") or {}).get("estimated_nutrition_per_100g") or {}).get("added_sugars_g")
    return _as_float(value)


__all__ = ["added_sugars_from_formatted", "added_sugars_per_100g", "enrich_added_sugars"]
