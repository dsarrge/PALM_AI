from __future__ import annotations

from contextlib import closing
import copy
from pathlib import Path
import sqlite3
from typing import Any

from food_ai.config import resolve_runtime_db_path
from food_ai.food_bridge.ingredient_normalize import normalize_ingredient
from food_ai.food_bridge.nutrition_bridge import lookup_ingredient_nutrition


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

COMMON_INGREDIENT_SUGGESTIONS = (
    "lasagna noodles",
    "tomato sauce",
    "ground beef",
    "ricotta cheese",
    "mozzarella cheese",
    "parmesan cheese",
    "olive oil",
    "butter",
    "egg",
    "milk",
    "flour",
    "sugar",
    "rice",
    "chicken breast",
    "cheddar cheese",
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


def _append_suggestions(
    *,
    connection: sqlite3.Connection,
    suggestions: dict[str, str],
    table: str,
    column: str,
    query: str,
    limit: int,
) -> None:
    if not _table_exists(connection, table) or column not in _table_columns(connection, table):
        return
    normalized_query = query.strip().lower()
    if normalized_query:
        rows = connection.execute(
            f"""
            SELECT DISTINCT {column} AS ingredient
            FROM {table}
            WHERE {column} IS NOT NULL
              AND TRIM({column}) != ''
              AND LOWER({column}) LIKE LOWER(?)
            ORDER BY
                CASE
                    WHEN LOWER({column}) = LOWER(?) THEN 0
                    WHEN LOWER({column}) LIKE LOWER(?) THEN 1
                    ELSE 2
                END,
                LENGTH({column}),
                {column}
            LIMIT ?
            """,
            (f"%{normalized_query}%", normalized_query, f"{normalized_query}%", max(1, int(limit))),
        ).fetchall()
    else:
        rows = connection.execute(
            f"""
            SELECT DISTINCT {column} AS ingredient
            FROM {table}
            WHERE {column} IS NOT NULL
              AND TRIM({column}) != ''
            ORDER BY LENGTH({column}), {column}
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()

    for row in rows:
        ingredient = str(row["ingredient"] if isinstance(row, sqlite3.Row) else row[0]).strip()
        key = ingredient.lower()
        if ingredient and key not in suggestions:
            suggestions[key] = ingredient


def load_ingredient_suggestions(
    query: str = "",
    *,
    db_path: str | Path | None = None,
    limit: int = 30,
) -> list[str]:
    clean_query = str(query or "").strip()
    suggestions: dict[str, str] = {}

    for ingredient in COMMON_INGREDIENT_SUGGESTIONS:
        if not clean_query or clean_query.lower() in ingredient.lower():
            suggestions[ingredient.lower()] = ingredient

    database_path = resolve_runtime_db_path(db_path)
    if not database_path.exists():
        return list(suggestions.values())[: max(1, int(limit))]

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        for table, column in (
            ("canonical_food_aliases", "alias_name"),
            ("canonical_foods", "canonical_name"),
            ("ingredient_aliases", "alias"),
            ("ingredient_aliases", "canonical_ingredient"),
            ("generic_ingredient_nutrition", "ingredient"),
            ("generic_ingredient_nutrition", "normalized_ingredient"),
            ("ingredient_nutrients", "ingredient"),
            ("ingredient_nutrients", "normalized_ingredient"),
            ("generic_foods", "food_name"),
            ("generic_foods", "name"),
            ("generic_foods", "description"),
        ):
            if len(suggestions) >= limit:
                break
            _append_suggestions(
                connection=connection,
                suggestions=suggestions,
                table=table,
                column=column,
                query=clean_query,
                limit=max(1, int(limit) - len(suggestions)),
            )

    return list(suggestions.values())[: max(1, int(limit))]


def manual_ingredient_key(row: dict[str, Any] | str) -> str:
    if isinstance(row, str):
        value = row
    else:
        value = row.get("normalized_ingredient") or row.get("ingredient") or ""
    text = str(value).strip()
    return normalize_ingredient(text) or text.lower()


def clean_manual_ingredient_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        ingredient = str(row.get("ingredient") or "").strip()
        grams = _as_float(row.get("grams_per_100g_dish"))
        if not ingredient or grams is None or grams <= 0.0:
            continue
        key = manual_ingredient_key(ingredient)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(
            {
                "include_in_nutrition": bool(row.get("include_in_nutrition", True)),
                "ingredient": ingredient,
                "normalized_ingredient": key,
                "grams_per_100g_dish": round(float(grams), 3),
            }
        )
    return cleaned


def manual_exclusion_keys(rows: list[dict[str, Any]] | None) -> list[str]:
    return [
        manual_ingredient_key(row)
        for row in clean_manual_ingredient_rows(rows)
        if not row.get("include_in_nutrition", True) and manual_ingredient_key(row)
    ]


def _manual_lookup_to_matched_row(
    manual_row: dict[str, Any],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    ingredient = str(manual_row["ingredient"])
    normalized = str(manual_row["normalized_ingredient"])
    grams = float(manual_row["grams_per_100g_dish"])
    nutrition = lookup.get("nutrition_per_100g") or {}
    return {
        "ingredient": ingredient,
        "normalized": normalized,
        "normalized_ingredient": normalized,
        "matched_ingredient": lookup.get("matched_ingredient") or normalized,
        "status": "found",
        "included_in_nutrition": bool(manual_row.get("include_in_nutrition", True)),
        "visibility_label": "user_manual",
        "ingredient_role": "user_manual",
        "weight": round(grams / 100.0, 6),
        "confidence": 1.0,
        "grams_default": grams,
        "grams_per_100g_dish": grams,
        "original_grams_per_100g_dish": grams,
        "contribution_basis": "ingredient_grams",
        "portion_method": "user_entered_grams_per_100g_dish",
        "weight_source": "user_manual",
        "source_count": 1,
        "recipe_pct": None,
        "match_method": lookup.get("match_method"),
        "matched_table": lookup.get("matched_table"),
        "matched_column": lookup.get("matched_column"),
        "fdc_id": lookup.get("fdc_id"),
        "source": lookup.get("matched_table"),
        "nutrition_source": lookup.get("matched_table"),
        "nutrition_per_100g": {key: nutrition.get(key) for key in NUTRIENT_KEYS},
        "reason": None,
        "manual_ingredient": True,
    }


def _manual_lookup_to_unmatched_row(
    manual_row: dict[str, Any],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    ingredient = str(manual_row["ingredient"])
    normalized = str(manual_row["normalized_ingredient"])
    grams = float(manual_row["grams_per_100g_dish"])
    return {
        "ingredient": ingredient,
        "normalized": normalized,
        "normalized_ingredient": normalized,
        "matched_ingredient": None,
        "status": "not_found",
        "included_in_nutrition": False,
        "visibility_label": "user_manual",
        "ingredient_role": "user_manual",
        "weight": round(grams / 100.0, 6),
        "grams_default": grams,
        "grams_per_100g_dish": grams,
        "contribution_basis": "ingredient_grams",
        "portion_method": "user_entered_grams_per_100g_dish",
        "weight_source": "user_manual",
        "match_method": lookup.get("match_method") or "not_found",
        "reason": lookup.get("reason") or "manual_ingredient_not_matched_to_nutrition",
        "nearest_candidates": lookup.get("nearest_candidates") or [],
        "manual_ingredient": True,
    }


def build_manual_ingredient_bridge_rows(
    manual_rows: list[dict[str, Any]] | None,
    db_path: str | Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    lookup_debug: list[dict[str, Any]] = []
    for row in clean_manual_ingredient_rows(manual_rows):
        lookup = lookup_ingredient_nutrition(row["ingredient"], db_path=db_path)
        lookup_debug.append(lookup)
        if lookup.get("status") == "found" and lookup.get("nutrition_per_100g"):
            matched.append(_manual_lookup_to_matched_row(row, lookup))
        else:
            unmatched.append(_manual_lookup_to_unmatched_row(row, lookup))
    return matched, unmatched, lookup_debug


def apply_manual_ingredients_to_bridge_result(
    bridge_result: dict[str, Any],
    manual_rows: list[dict[str, Any]] | None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(bridge_result)
    matched, unmatched, lookup_debug = build_manual_ingredient_bridge_rows(manual_rows, db_path=db_path)
    if not matched and not unmatched:
        return result

    existing_ranked = result.get("ingredients_ranked") or []
    existing_visible = result.get("visible_ingredients") or existing_ranked
    manual_ranked = matched + unmatched
    result["ingredients_ranked"] = existing_ranked + manual_ranked
    result["visible_ingredients"] = existing_visible + manual_ranked

    palm_ready = dict(result.get("palm_ready") or {})
    palm_ready["matched_ingredients"] = list(palm_ready.get("matched_ingredients") or []) + matched
    palm_ready["unmatched_ingredients"] = list(palm_ready.get("unmatched_ingredients") or []) + unmatched
    palm_ready["manual_ingredients"] = {
        "requested_count": len(clean_manual_ingredient_rows(manual_rows)),
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "lookups": lookup_debug,
    }
    result["palm_ready"] = palm_ready

    normalization = copy.deepcopy(result.get("ingredient_normalization") or {})
    if isinstance(normalization, dict):
        normalization["matched"] = list(normalization.get("matched") or []) + [
            {
                "raw_ingredient": row.get("ingredient"),
                "normalized": row.get("normalized_ingredient"),
                "canonical": row.get("matched_ingredient"),
                "matched_ingredient": row.get("matched_ingredient"),
                "usda_match_label": row.get("matched_ingredient"),
                "usda_match_method": row.get("match_method"),
                "confidence": 1.0,
                "weight_source": "user_manual",
                "included_in_nutrition": row.get("included_in_nutrition", True),
            }
            for row in matched
        ]
        normalization["unmatched"] = list(normalization.get("unmatched") or []) + [
            {
                "ingredient": row.get("ingredient"),
                "normalized": row.get("normalized_ingredient"),
                "reason": row.get("reason"),
                "weight_source": "user_manual",
                "included_in_nutrition": False,
            }
            for row in unmatched
        ]
        result["ingredient_normalization"] = normalization

    warnings = list(result.get("warnings") or [])
    if unmatched:
        warnings.append("manual_ingredient_lookup_partial")
    result["warnings"] = list(dict.fromkeys(warnings))
    return result


__all__ = [
    "apply_manual_ingredients_to_bridge_result",
    "build_manual_ingredient_bridge_rows",
    "clean_manual_ingredient_rows",
    "load_ingredient_suggestions",
    "manual_exclusion_keys",
    "manual_ingredient_key",
]
