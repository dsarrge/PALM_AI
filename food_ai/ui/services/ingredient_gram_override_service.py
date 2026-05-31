from __future__ import annotations

import copy
from typing import Any

from food_ai.food_bridge.ingredient_normalize import normalize_ingredient


def _as_float(value: Any) -> float | None:
    if value in (None, "", "n/a"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ingredient_override_key(row: dict[str, Any] | str) -> str:
    if isinstance(row, str):
        text = row
    else:
        text = row.get("normalized_ingredient") or row.get("normalized") or row.get("ingredient") or ""
    normalized = normalize_ingredient(str(text)) or str(text).strip().lower()
    return normalized.strip().lower()


def clean_gram_overrides(overrides: dict[str, Any] | None) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for key, value in (overrides or {}).items():
        grams = _as_float(value)
        if grams is None or grams < 0.0:
            continue
        normalized_key = ingredient_override_key(str(key))
        if normalized_key:
            cleaned[normalized_key] = round(float(grams), 3)
    return cleaned


def _apply_to_row(row: dict[str, Any], overrides: dict[str, float]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    updated = dict(row)
    key = ingredient_override_key(updated)
    if not key or key not in overrides:
        return updated, None

    new_grams = overrides[key]
    old_grams = _as_float(updated.get("grams_per_100g_dish") or updated.get("grams_default"))
    if old_grams is not None and abs(old_grams - new_grams) < 0.0005:
        return updated, None

    updated["user_grams_override_applied"] = True
    updated["user_grams_override_original_grams_per_100g_dish"] = old_grams
    updated["grams_per_100g_dish"] = new_grams
    updated["grams_default"] = new_grams
    updated["contribution_basis"] = "ingredient_grams"
    updated["portion_method"] = "user_edited_grams_per_100g_dish"
    updated["weight_source"] = "user_edited_grams_per_100g_dish"
    return updated, {
        "ingredient": updated.get("ingredient"),
        "matched_ingredient": updated.get("matched_ingredient"),
        "normalized_ingredient": key,
        "original_grams_per_100g_dish": old_grams,
        "edited_grams_per_100g_dish": new_grams,
    }


def _apply_to_rows(rows: list[dict[str, Any]], overrides: dict[str, float]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    updated_rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        updated, audit = _apply_to_row(row, overrides)
        updated_rows.append(updated)
        if audit is not None:
            audits.append(audit)
    return updated_rows, audits


def apply_gram_overrides_to_result(
    inference_result: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    cleaned_overrides = clean_gram_overrides(overrides)
    result = copy.deepcopy(inference_result)
    if not cleaned_overrides:
        return result

    all_audits: list[dict[str, Any]] = []
    for key in ("ingredients_ranked", "visible_ingredients"):
        rows = result.get(key) or []
        if isinstance(rows, list):
            result[key], audits = _apply_to_rows(rows, cleaned_overrides)
            all_audits.extend(audits)

    palm_ready = dict(result.get("palm_ready") or {})
    for key in ("matched_ingredients", "unmatched_ingredients"):
        rows = palm_ready.get(key) or []
        if isinstance(rows, list):
            palm_ready[key], audits = _apply_to_rows(rows, cleaned_overrides)
            all_audits.extend(audits)

    if all_audits:
        deduped = {}
        for audit in all_audits:
            deduped[str(audit.get("normalized_ingredient") or audit.get("ingredient") or "")] = audit
        palm_ready["ingredient_gram_overrides"] = {
            "applied_count": len(deduped),
            "audits": list(deduped.values()),
        }
        warnings = list(result.get("warnings") or [])
        warnings.append("user_ingredient_gram_overrides_applied")
        result["warnings"] = list(dict.fromkeys(warnings))

    result["palm_ready"] = palm_ready
    return result


__all__ = ["apply_gram_overrides_to_result", "clean_gram_overrides", "ingredient_override_key"]
