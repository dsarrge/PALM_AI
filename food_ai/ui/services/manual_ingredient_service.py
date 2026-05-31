from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

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


def _as_float(value: Any) -> float | None:
    if value in (None, "", "n/a"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    "manual_exclusion_keys",
    "manual_ingredient_key",
]
