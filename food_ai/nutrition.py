from __future__ import annotations

from typing import Any


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_nutrition(nutrition: dict[str, Any]) -> dict[str, Any]:
    calories = _as_float(nutrition.get("calories"))
    protein_g = _as_float(nutrition.get("protein_g"))
    fat_g = _as_float(nutrition.get("total_fat_g", nutrition.get("fat_g")))
    carbs_g = _as_float(nutrition.get("total_carbs_g", nutrition.get("carbs_g")))
    calories_from_macros = protein_g * 4.0 + fat_g * 9.0 + carbs_g * 4.0
    fat_calories = fat_g * 9.0
    fat_pct = (fat_calories / calories_from_macros) if calories_from_macros > 0.0 else 0.0
    label_consistency = calories <= 0.0 or abs(calories - calories_from_macros) <= max(80.0, calories * 0.35)
    return {
        "normalized": {
            "calories": calories,
            "protein_g": protein_g,
            "total_fat_g": fat_g,
            "total_carbs_g": carbs_g,
        },
        "derived": {
            "calories_from_macros": calories_from_macros,
            "fat_pct": fat_pct,
        },
        "quality": {
            "label_consistency": bool(label_consistency),
        },
    }
