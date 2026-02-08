# -*- coding: utf-8 -*-
"""
Created on Sat Feb  7 18:14:08 2026

@author: dsarr

PALM Food AI
Nutrition Engine
food_ai/core/nutrition_engine.py

Assumptions:
- Input nutrition values are PER 100 GRAMS
- Scaling to actual intake happens elsewhere
"""

from datetime import datetime

MACRO_CALORIES = {
    "protein_g": 4,
    "carbs_g": 4,
    "fat_g": 9
}

def normalize_nutrition(per_100g: dict) -> dict:
    """
    Normalize and validate nutrition data given per-100g inputs.

    Returns:
        {
            per_gram: dict,
            per_100g: dict,
            derived: dict,
            quality: dict,
            meta: dict
        }
    """
    
    # Per-gram normalization
    per_gram = {}
    missing = []

    for k, v in per_100g.items():
        if k.endswith(("_g", "_mg")) or k == "calories":
            if v is None:
                missing.append(k)
                continue
            per_gram[k] = v / 100.0

    # Derived macro calories
    calories_from_macros = sum(
        per_100g.get(m, 0) * c
        for m, c in MACRO_CALORIES.items()
    )

    macro_diff = abs(
        per_100g.get("calories", 0) - calories_from_macros
    )

    total_macro_cals = calories_from_macros or 1

    derived = {
        "calories_from_macros": calories_from_macros,
        "macro_calorie_diff": macro_diff,
        "protein_pct": (per_100g.get("protein_g", 0) * 4) / total_macro_cals,
        "fat_pct": (per_100g.get("fat_g", 0) * 9) / total_macro_cals,
        "carb_pct": (per_100g.get("carbs_g", 0) * 4) / total_macro_cals,
    }

    # Quality scoring
    confidence = 1.0

    if macro_diff > 50:
        confidence -= 0.3

    if missing:
        confidence -= 0.2

    confidence = max(0.0, min(confidence, 1.0))

    # Final output
    per_100g_clean = {
        k: v for k, v in per_100g.items()
        if k.endswith(("_g", "_mg")) or k == "calories"
    }
    
    macros = {
        "calories": per_100g_clean.get("calories"),
        "total_fat_g": per_100g_clean.get("total_fat_g"),
        "saturated_fat_g": per_100g_clean.get("saturated_fat_g"),
        "trans_fat_g": per_100g_clean.get("trans_fat_g"),
        "total_carbs_g": per_100g_clean.get("total_carbs_g"),
        "fiber_g": per_100g_clean.get("fiber_g"),
        "total_sugars_g": per_100g_clean.get("total_sugars_g"),
        "added_sugars_g": per_100g_clean.get("added_sugars_g"),
        "protein_g": per_100g_clean.get("protein_g"),
        "sodium_mg": per_100g_clean.get("sodium_mg"),
        "cholesterol_mg": per_100g_clean.get("cholesterol_mg"),
    }

    micros = {
        "vitamin_d_mcg": per_100g_clean.get("vitamin_d_mcg"),
        "calcium_mg": per_100g_clean.get("calcium_mg"),
        "iron_mg": per_100g_clean.get("iron_mg"),
        "potassium_mg": per_100g_clean.get("potassium_mg"),
        "caffeine_mg": per_100g_clean.get("caffeine_mg"),
    }
    # Derivations
    if macros["total_carbs_g"] is None:
        sugar = macros.get("total_sugars_g")
        fiber = macros.get("fiber_g")

        if sugar is not None and fiber is not None:
            macros["total_carbs_g"] = sugar + fiber
        elif sugar is not None:
            macros["total_carbs_g"] = sugar
            
    if macros["total_sugars_g"] is None and macros["added_sugars_g"] is not None:
        macros["total_sugars_g"] = macros["added_sugars_g"]

    return {
        "per_gram": per_gram,
        "per_100g": per_100g_clean,
        "macros": macros,
        "micros": micros,
        "derived": derived,
        "quality": {
            "confidence": confidence,
            "label_consistency": macro_diff < 50,
            "missing_fields": missing
        },
        "meta": {
            "normalized_at": datetime.utcnow().isoformat()
        }
    }


# def validate_per_100g(per_100g: dict) -> bool:
#     return any(v is not None and v > 0 for v in per_100g.values())
