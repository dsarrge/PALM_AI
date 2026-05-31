from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from food_ai.food_bridge.ingredient_normalize import normalize_ingredient
from food_ai.food_bridge.nutrition_bridge import NUTRIENT_KEYS, lookup_ingredient_nutrition


COOKED_DISH_KEYWORDS = (
    "lasagna",
    "spaghetti",
    "macaroni",
    "ravioli",
    "gnocchi",
    "fettuccine",
    "risotto",
    "fried rice",
    "pizza",
    "hamburger",
    "hot dog",
    "taco",
    "burrito",
    "omelet",
    "omelette",
)

COOKED_INGREDIENT_DEFAULTS = {
    "ground beef": "browned ground beef",
    "beef ground": "browned ground beef",
    "lasagna noodle": "cooked lasagna noodles",
    "pasta": "cooked pasta",
    "spaghetti": "cooked spaghetti",
    "macaroni": "cooked macaroni",
    "penne": "cooked penne pasta",
    "rigatoni": "cooked rigatoni pasta",
    "fettuccine": "cooked fettuccine",
    "rice": "cooked rice",
    "sausage": "cooked sausage",
    "chicken breast": "cooked chicken breast",
    "chicken": "cooked chicken",
    "egg": "cooked egg",
}


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _top_dish_label(result: dict[str, Any]) -> str:
    fused = result.get("dish_fused_topk") or result.get("dish_classifier_topk") or []
    if not fused:
        return ""
    top = fused[0] or {}
    return str(top.get("label") or top.get("name") or "").replace("_", " ").strip().lower()


def _is_cooked_dish(result: dict[str, Any]) -> bool:
    label = _top_dish_label(result)
    return any(keyword in label for keyword in COOKED_DISH_KEYWORDS)


def _ingredient_text(row: dict[str, Any]) -> str:
    return str(row.get("ingredient") or row.get("matched_ingredient") or row.get("normalized") or "").strip()


def _dedupe_key(row: dict[str, Any]) -> str:
    text = _ingredient_text(row)
    normalized = normalize_ingredient(text) or text.lower().strip()
    return normalized


def _row_score(row: dict[str, Any]) -> tuple[float, float, float, float, str]:
    include = 1.0 if bool(row.get("included_in_nutrition", row.get("default_include", True))) else 0.0
    grams = _as_float(row.get("grams_per_100g_dish") or row.get("grams_default"))
    source_count = _as_float(row.get("source_count") or row.get("recipe_count") or row.get("n_weight_observations"))
    confidence = _as_float(row.get("confidence") or row.get("confidence_score"))
    return include, grams, source_count, confidence, _ingredient_text(row)


def _apply_cooked_default(row: dict[str, Any], *, cooked_dish: bool) -> tuple[dict[str, Any], dict[str, Any] | None]:
    updated = dict(row)
    ingredient = _ingredient_text(updated)
    normalized = normalize_ingredient(ingredient) or ingredient.lower().strip()
    if not cooked_dish or normalized not in COOKED_INGREDIENT_DEFAULTS:
        return updated, None

    cooked_name = COOKED_INGREDIENT_DEFAULTS[normalized]
    if ingredient.lower().strip() == cooked_name.lower():
        return updated, None

    updated["original_ingredient"] = ingredient
    updated["ingredient"] = cooked_name
    updated["normalized"] = normalized
    updated["normalized_ingredient"] = normalized
    updated["prep_default_applied"] = True
    updated["prep_default_reason"] = "cooked_dish_default"
    return updated, {
        "original_ingredient": ingredient,
        "selected_ingredient": cooked_name,
        "normalized_ingredient": normalized,
        "reason": "cooked_dish_default",
    }


def _dedupe_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    best_by_key: dict[str, dict[str, Any]] = {}
    duplicate_audits: list[dict[str, Any]] = []
    for row in rows:
        key = _dedupe_key(row)
        if not key:
            continue
        existing = best_by_key.get(key)
        if existing is None or _row_score(row) > _row_score(existing):
            if existing is not None:
                duplicate_audits.append(
                    {
                        "key": key,
                        "kept": _ingredient_text(row),
                        "removed": _ingredient_text(existing),
                        "reason": "stronger_candidate_replaced_duplicate",
                    }
                )
            best_by_key[key] = row
        else:
            duplicate_audits.append(
                {
                    "key": key,
                    "kept": _ingredient_text(existing),
                    "removed": _ingredient_text(row),
                    "reason": "duplicate_candidate_filtered",
                }
            )
    return list(best_by_key.values()), duplicate_audits


def _lookup_to_matched(row: dict[str, Any], lookup: dict[str, Any]) -> dict[str, Any]:
    nutrition = lookup.get("nutrition_per_100g") or {}
    return {
        **row,
        "ingredient": _ingredient_text(row),
        "normalized": lookup.get("normalized_ingredient") or row.get("normalized") or _dedupe_key(row),
        "normalized_ingredient": lookup.get("normalized_ingredient") or row.get("normalized_ingredient") or _dedupe_key(row),
        "matched_ingredient": lookup.get("matched_ingredient") or row.get("matched_ingredient"),
        "matched_table": lookup.get("matched_table"),
        "matched_column": lookup.get("matched_column"),
        "match_method": lookup.get("match_method"),
        "match_score": lookup.get("match_score"),
        "status": "found",
        "nutrition_source": lookup.get("matched_table") or "usda_generic",
        "nutrition_per_100g": {key: nutrition.get(key) for key in NUTRIENT_KEYS},
        "source": lookup.get("matched_table"),
        "fdc_id": lookup.get("fdc_id"),
        "included_in_nutrition": bool(row.get("included_in_nutrition", row.get("default_include", True))),
    }


def _lookup_to_unmatched(row: dict[str, Any], lookup: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "ingredient": _ingredient_text(row),
        "normalized": lookup.get("normalized_ingredient") or row.get("normalized") or _dedupe_key(row),
        "normalized_ingredient": lookup.get("normalized_ingredient") or row.get("normalized_ingredient") or _dedupe_key(row),
        "matched_ingredient": None,
        "status": "not_found",
        "included_in_nutrition": False,
        "nutrition_source": "not_found",
        "match_method": lookup.get("match_method") or "not_found",
        "reason": lookup.get("reason") or "candidate_lookup_not_found",
        "nearest_candidates": lookup.get("nearest_candidates") or [],
    }


def clean_ingredient_candidates_for_nutrition(
    inference_result: dict[str, Any],
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Apply pre-nutrition candidate cleanup for production meal logging.

    This filters semantic duplicates before the reviewed nutrition calculation and
    defaults cooked dishes to cooked ingredient names where the normalized ingredient
    identity is stable. Nutrition still comes from PALM's existing DB lookup path.
    """
    result = copy.deepcopy(inference_result)
    cooked_dish = _is_cooked_dish(result)

    source_rows = result.get("ingredients_ranked") or result.get("visible_ingredients") or []
    prepared_rows: list[dict[str, Any]] = []
    prep_audits: list[dict[str, Any]] = []
    for row in source_rows:
        if not isinstance(row, dict):
            continue
        prepared, audit = _apply_cooked_default(row, cooked_dish=cooked_dish)
        prepared_rows.append(prepared)
        if audit is not None:
            prep_audits.append(audit)

    deduped_rows, duplicate_audits = _dedupe_rows(prepared_rows)
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    lookup_audits: list[dict[str, Any]] = []
    for row in deduped_rows:
        lookup = lookup_ingredient_nutrition(_ingredient_text(row), db_path=db_path)
        lookup_audits.append(
            {
                "ingredient": _ingredient_text(row),
                "status": lookup.get("status"),
                "matched_ingredient": lookup.get("matched_ingredient"),
                "match_method": lookup.get("match_method"),
                "reason": lookup.get("reason"),
            }
        )
        if lookup.get("status") == "found" and lookup.get("nutrition_per_100g"):
            matched.append(_lookup_to_matched(row, lookup))
        else:
            unmatched.append(_lookup_to_unmatched(row, lookup))

    result["ingredients_ranked"] = deduped_rows
    result["visible_ingredients"] = [row for row in deduped_rows if not str(row.get("visibility_label") or "").startswith("hidden")]

    palm_ready = dict(result.get("palm_ready") or {})
    palm_ready["matched_ingredients"] = matched
    palm_ready["unmatched_ingredients"] = unmatched
    palm_ready["ingredient_candidate_cleanup"] = {
        "cooked_dish_default_applied": bool(prep_audits),
        "cooked_dish_detected": cooked_dish,
        "input_count": len(source_rows),
        "deduped_count": len(deduped_rows),
        "duplicate_count": len(duplicate_audits),
        "prep_default_count": len(prep_audits),
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "prep_audits": prep_audits,
        "duplicate_audits": duplicate_audits,
        "lookup_audits": lookup_audits,
    }
    result["palm_ready"] = palm_ready

    normalization = copy.deepcopy(result.get("ingredient_normalization") or {})
    if isinstance(normalization, dict):
        normalization["matched"] = matched
        normalization["unmatched"] = unmatched
        normalization["summary"] = {
            "matched_count": len(matched),
            "unmatched_count": len(unmatched),
            "deduped_count": len(deduped_rows),
            "duplicate_count": len(duplicate_audits),
        }
        result["ingredient_normalization"] = normalization

    warnings = list(result.get("warnings") or [])
    if duplicate_audits:
        warnings.append("duplicate_ingredient_candidates_filtered")
    if prep_audits:
        warnings.append("cooked_ingredient_defaults_applied")
    if unmatched:
        warnings.append("ingredient_candidate_cleanup_unmatched_rows")
    result["warnings"] = list(dict.fromkeys(warnings))
    return result


__all__ = ["clean_ingredient_candidates_for_nutrition"]
