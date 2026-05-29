from __future__ import annotations

import argparse
import csv
import copy
import json
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

from food_ai.config import DATA_DIR
from food_ai.config import resolve_runtime_db_path
from food_ai.food_bridge.config import (
    DB_PATH,
    DISH_MASS_PRIORS_PATH,
    DISH_PRIORS_PATH,
    MIN_RECIPE_MATCHES,
    NUTRITION5K_MASS_PRIORS_PATH,
    NUTRITION5K_MIN_MATCHED_DISHES,
    NUTRITION5K_MIN_OVERLAP_SCORE,
    NUTRITION5K_TOP_MATCHES,
)
from food_ai.food_bridge.ingredient_normalize import CANONICAL_INGREDIENT_ALIASES, ingredient_aliases_for, normalize_ingredient
from food_ai.food_bridge.types import BridgeResult, IngredientNutrition

try:
    from food_ai.usda.matching import lexical_match_score
except Exception:  # pragma: no cover - optional local helper
    lexical_match_score = None


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

FAT_COMPONENT_KEYS = (
    "saturated_fat_g",
    "monounsaturated_fat_g",
    "polyunsaturated_fat_g",
    "trans_fat_g",
)

DISH_MASS_MIN_APPLIED_INGREDIENTS = 3

REQUIRED_TOP_DISH_INGREDIENTS = {
    "lasagna": {"lasagna noodle"},
}

SEASONING_WEIGHT_CAPS = {
    "baking powder": 0.02,
    "baking soda": 0.003,
    "cinnamon": 0.004,
    "nutmeg": 0.005,
    "salt": 0.007,
    "vanilla": 0.004,
}

DESSERT_LABEL_HINTS = {
    "cake",
    "cup_cakes",
    "doughnuts",
    "donuts",
    "ice_cream",
    "pancakes",
    "waffles",
}

LOCAL_NUTRITION_ALIASES = {
    "all purpose": ["all purpose flour", "whole wheat flour"],
    "all purpose flour": ["all purpose flour", "whole wheat flour"],
    "all-purpose flour": ["all purpose flour", "whole wheat flour"],
    "basil": ["basil", "fresh basil"],
    "butter": ["butter unsalted", "butter salted"],
    "butter salted": ["butter salted"],
    "butter unsalted": ["butter unsalted"],
    "canola oil": ["canola oil", "olive oil"],
    "cheddar": ["cheddar cheese", "cheddar"],
    "cheddar cheese": ["cheddar cheese"],
    "cheese blend": ["cheese"],
    "chicken breast": ["chicken breast", "chicken"],
    "cream cheese": ["cream cheese"],
    "cottage cheese": ["cottage cheese"],
    "egg": ["egg whole"],
    "eggs": ["egg whole"],
    "elbow macaroni": ["macaroni"],
    "fettuccine": ["fettuccine", "pasta"],
    "fettuccine noodle": ["fettuccine", "pasta"],
    "flour": ["all purpose flour", "whole wheat flour"],
    "fresh basil": ["basil", "fresh basil"],
    "ground beef": ["beef ground", "ground beef", "beef"],
    "italian cheese blend": ["cheese"],
    "italian sausage": ["pork sausage", "beef sausage", "sausage"],
    "lasagna noodle": ["lasagna noodle", "lasagna noodles", "pasta"],
    "lasagna noodles": ["lasagna noodles", "lasagna noodle", "pasta"],
    "lasagne noodle": ["lasagna noodle", "pasta"],
    "macaroni": ["macaroni", "pasta"],
    "macaroni noodle": ["macaroni", "pasta"],
    "marinara sauce": ["tomato paste", "tomato"],
    "milk": ["milk", "whole milk"],
    "mozzarella": ["mozzarella", "mozzarella cheese"],
    "mozzarella cheese": ["mozzarella", "mozzarella cheese"],
    "noodle": ["pasta"],
    "oil": ["canola oil", "olive oil", "coconut oil"],
    "olive oil": ["olive oil"],
    "parmesan": ["parmesan", "parmesan cheese"],
    "parmesan cheese": ["parmesan", "parmesan cheese"],
    "pasta noodle": ["pasta"],
    "pasta sauce": ["tomato paste", "tomato"],
    "pecorino romano": ["romano cheese", "parmesan cheese"],
    "penne": ["penne", "pasta"],
    "penne pasta": ["penne", "pasta"],
    "pork sausage": ["pork sausage", "beef sausage", "sausage"],
    "powdered sugar": ["powdered sugar", "sugar"],
    "confectioner sugar": ["powdered sugar", "sugar"],
    "confectioners sugar": ["powdered sugar", "sugar"],
    "provolone": ["provolone cheese", "cheese"],
    "provolone cheese": ["provolone cheese", "cheese"],
    "ricotta": ["ricotta cheese", "cottage cheese", "cheese"],
    "ricotta cheese": ["ricotta cheese", "cottage cheese", "cheese"],
    "rigatoni": ["rigatoni", "pasta"],
    "rigatoni pasta": ["rigatoni", "pasta"],
    "romano cheese": ["romano cheese", "parmesan cheese"],
    "salted butter": ["butter salted"],
    "sausage": ["pork sausage", "beef sausage", "chicken sausage", "turkey sausage"],
    "spaghetti": ["spaghetti", "pasta"],
    "spaghetti noodle": ["spaghetti", "pasta"],
    "spaghetti pasta": ["spaghetti", "pasta"],
    "sugar": ["sugar", "granulated sugar"],
    "swiss cheese": ["swiss cheese"],
    "tomato paste": ["tomato paste", "tomato"],
    "tomato sauce": ["tomato sauce", "tomato paste", "tomatoes", "tomato"],
    "unsalted butter": ["butter unsalted"],
    "vanilla": ["vanilla extract", "vanilla"],
    "vanilla extract": ["vanilla extract"],
    "vegetable oil": ["olive oil", "canola oil"],
    "wheat flour": ["all purpose flour", "whole wheat flour"],
    "whole milk": ["milk", "whole milk"],
    "whole egg": ["egg whole"],
    "buttermilk": ["milk"],
}

CANONICAL_NUTRITION_ROW_SOURCES = {
    "butter": "butter unsalted",
    "cheddar": "cheddar cheese",
    "egg": "egg whole",
    "ground beef": "beef ground",
    "lasagna noodle": "lasagna noodles",
    "mozzarella": "mozzarella cheese",
    "oil": "canola oil",
    "parmesan": "parmesan cheese",
    "ricotta": "ricotta cheese",
    "sausage": "pork sausage",
    "tomato sauce": "tomato paste",
    "wheat flour": "all purpose flour",
}

USDA_FOOD_CSV_PATH = DATA_DIR / "USDA" / "food.csv"
ALLOWED_USDA_DATA_TYPES = {"foundation_food", "sr_legacy_food"}

OIL_TOKENS = {"oil", "olive", "canola", "coconut"}
MILK_TOKENS = {"milk", "buttermilk"}
CHEESE_TOKENS = {
    "cheese",
    "mozzarella",
    "parmesan",
    "cheddar",
    "ricotta",
    "cottage",
    "provolone",
    "swiss",
    "romano",
    "pecorino",
    "cream",
}

LOOKUP_TABLE_ORDER = (
    "generic_ingredient_nutrition",
    "ingredient_nutrients",
    "generic_food_nutrition_join_generic_foods",
)

AUDIT_TABLES = (
    "generic_ingredient_nutrition",
    "generic_food_nutrition",
    "ingredient_nutrients",
    "generic_foods",
    "nutrition_facts",
)

NAME_COLUMN_CANDIDATES = (
    "ingredient",
    "normalized_ingredient",
    "food_name",
    "name",
    "description",
    "usda_description",
)

NUTRIENT_COLUMN_CANDIDATES = {
    "calories": ("calories",),
    "protein_g": ("protein_g",),
    "total_fat_g": ("total_fat_g", "fat_g"),
    "saturated_fat_g": ("saturated_fat_g", "sat_fat_g"),
    "monounsaturated_fat_g": ("monounsaturated_fat_g", "mono_fat_g"),
    "polyunsaturated_fat_g": ("polyunsaturated_fat_g", "poly_fat_g"),
    "trans_fat_g": ("trans_fat_g",),
    "total_carbs_g": ("total_carbs_g", "carbs_g"),
    "fiber_g": ("fiber_g",),
    "total_sugars_g": ("total_sugars_g", "sugar_g"),
    "sodium_mg": ("sodium_mg",),
    "cholesterol_mg": ("cholesterol_mg",),
    "source": ("source",),
    "fdc_id": ("fdc_id",),
    "description": ("description", "usda_description"),
}

AUDIT_INGREDIENTS = (
    "mozzarella",
    "parmesan",
    "ground beef",
    "basil",
    "tomato sauce",
    "olive oil",
    "lasagna noodle",
    "egg",
    "wheat flour",
    "vanilla",
    "butter",
    "sugar",
    "milk",
    "chicken breast",
    "rice",
    "pasta",
    "cheddar",
    "cream cheese",
    "banana",
    "potato",
)

COMMON_AUDIT_INGREDIENTS = (
    "spaghetti noodles",
    "lasagna noodles",
    "ricotta cheese",
    "mozzarella cheese",
    "parmesan cheese",
    "cheddar cheese",
    "cream cheese",
    "ground beef",
    "tomato sauce",
    "marinara sauce",
    "olive oil",
    "egg",
    "butter",
    "wheat flour",
)

def _common_ingredient_nutrition_alias_rows() -> tuple[tuple[str, str], ...]:
    rows: dict[str, str] = {}
    for canonical, source in CANONICAL_NUTRITION_ROW_SOURCES.items():
        rows.setdefault(canonical, source)
    return tuple(sorted(rows.items()))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_float(value: Any, digits: int = 6) -> float | None:
    numeric = _as_float(value)
    return round(numeric, digits) if numeric is not None else None


def _pending_result(
    dish_predictions: list[dict[str, Any]],
    warning: str,
    ingredients_ranked: list[dict[str, Any]] | None = None,
    ingredient_normalization: dict[str, Any] | None = None,
) -> BridgeResult:
    palm_ready = {
        "status": "pending",
        "label": "Estimated nutrition per 100g from dish ingredient priors",
        "estimated_nutrition_per_100g": None,
        "matched_ingredients": [],
        "unmatched_ingredients": [],
        "note": "This is an ingredient-prior estimate, not a portion-estimated meal result.",
        "reason": warning,
    }
    return BridgeResult(
        status="pending",
        dish_predictions=dish_predictions,
        ingredients_ranked=ingredients_ranked or [],
        visible_ingredients=ingredients_ranked or [],
        ingredient_normalization=ingredient_normalization or {"matched": [], "unmatched": [], "summary": {"matched_count": 0, "unmatched_count": 0}},
        palm_ready=palm_ready,
        warnings=[warning],
    )


def _load_priors(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        raise FileNotFoundError("Dish priors artifact missing. Run python -m food_ai.food_bridge.build_dish_priors")
    payload = json.loads(path.read_text(encoding="utf-8"))
    priors = payload.get("dish_priors")
    if not isinstance(priors, dict):
        raise ValueError(f"Dish priors artifact has no dish_priors object: {path}")
    return priors


def _prediction_confidence(prediction: dict[str, Any]) -> float:
    value = prediction.get("confidence", prediction.get("score", 0.0))
    return max(0.0, float(value or 0.0))


def _blend_ingredients(
    dish_predictions: list[dict[str, Any]],
    priors: dict[str, list[dict[str, Any]]],
    top_k_dishes: int,
    top_k_ingredients: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    blended: dict[str, dict[str, Any]] = defaultdict(lambda: {"weight": 0.0, "source_count": 0, "dish_sources": []})
    missing_priors: list[str] = []
    total_prediction_weight = 0.0

    for prediction in dish_predictions[: max(1, top_k_dishes)]:
        label = str(prediction.get("label") or "").strip()
        confidence = _prediction_confidence(prediction)
        if not label or confidence <= 0.0:
            continue
        dish_prior = priors.get(label)
        if not dish_prior:
            missing_priors.append(label)
            continue
        total_prediction_weight += confidence
        for ingredient_record in dish_prior:
            ingredient = normalize_ingredient(str(ingredient_record.get("ingredient") or ""))
            if not ingredient:
                continue
            prior_weight = float(ingredient_record.get("weight") or 0.0)
            contribution = confidence * prior_weight
            bucket = blended[ingredient]
            bucket["weight"] += contribution
            bucket["source_count"] += int(ingredient_record.get("source_count") or 0)
            bucket["dish_sources"].append({"label": label, "confidence": confidence, "prior_weight": prior_weight})

    if not blended or total_prediction_weight <= 0.0:
        return [], missing_priors

    total = sum(float(item["weight"]) for item in blended.values())
    ranked = []
    for ingredient, payload in blended.items():
        weight = float(payload["weight"]) / total if total else 0.0
        ranked.append(
            {
                "ingredient": ingredient,
                "confidence": round(weight, 6),
                "weight": round(weight, 6),
                "weight_source": "recipenlg_frequency",
                "visibility_label": "dish_prior",
                "recipe_hits": int(payload["source_count"]),
                "source_count": int(payload["source_count"]),
                "dish_sources": payload["dish_sources"],
            }
        )
    ranked.sort(key=lambda item: (float(item["weight"]), int(item["source_count"]), str(item["ingredient"])), reverse=True)
    return ranked, missing_priors


def _apply_recipe_weight_caps(
    ingredients_ranked: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    adjusted: list[dict[str, Any]] = []
    capped_count = 0
    for record in ingredients_ranked:
        updated = dict(record)
        ingredient = str(updated.get("ingredient") or "")
        weight_source = str(updated.get("weight_source") or "recipenlg_frequency")
        current_weight = float(updated.get("weight") or 0.0)
        max_weight = SEASONING_WEIGHT_CAPS.get(ingredient)
        if weight_source == "recipenlg_frequency" and max_weight is not None and current_weight > max_weight:
            updated["weight_before_cap"] = round(current_weight, 6)
            updated["weight"] = max_weight
            updated["confidence"] = max_weight
            updated["weight_adjustment"] = "seasoning_cap"
            capped_count += 1
        adjusted.append(updated)
    total_weight = sum(float(item.get("weight") or 0.0) for item in adjusted)
    if total_weight <= 0.0:
        return ingredients_ranked, 0
    for item in adjusted:
        normalized_weight = float(item.get("weight") or 0.0) / total_weight
        item["weight"] = round(normalized_weight, 6)
        item["confidence"] = round(normalized_weight, 6)
    adjusted.sort(key=lambda item: (float(item["weight"]), int(item.get("source_count") or 0), str(item["ingredient"])), reverse=True)
    return adjusted, capped_count


def _load_nutrition5k_mass_priors(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            "nutrition5k_artifact_missing: Run python -m food_ai.food_bridge.build_nutrition5k_mass_priors"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    dishes = payload.get("nutrition5k_dishes")
    if not isinstance(dishes, dict):
        raise ValueError("nutrition5k_missing_required_columns: artifact has no nutrition5k_dishes object")
    return payload


def _nutrition5k_dish_ingredient_map(dish: dict[str, Any]) -> dict[str, float]:
    mapped: dict[str, float] = {}
    for ingredient in dish.get("ingredients") or []:
        name = normalize_ingredient(str(ingredient.get("ingredient") or ""))
        mass_fraction = _as_float(ingredient.get("mass_fraction"))
        if name and mass_fraction is not None and mass_fraction > 0.0:
            mapped[name] = mapped.get(name, 0.0) + mass_fraction
    return mapped


def _nutrition5k_overlap_matches(
    ingredients_ranked: list[dict[str, Any]],
    nutrition5k_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    current_ingredients = {
        normalize_ingredient(str(item.get("ingredient") or ""))
        for item in ingredients_ranked
        if normalize_ingredient(str(item.get("ingredient") or ""))
    }
    if not current_ingredients:
        return []

    matches: list[dict[str, Any]] = []
    for dish_id, dish in (nutrition5k_payload.get("nutrition5k_dishes") or {}).items():
        mass_map = _nutrition5k_dish_ingredient_map(dish)
        if not mass_map:
            continue
        overlap = sorted(current_ingredients & set(mass_map))
        overlap_score = len(overlap) / max(1, len(current_ingredients))
        if overlap_score < NUTRITION5K_MIN_OVERLAP_SCORE:
            continue
        matches.append(
            {
                "dish_id": str(dish_id),
                "overlap_score": round(overlap_score, 6),
                "overlap_count": len(overlap),
                "overlap_ingredients": overlap,
                "total_mass_g": dish.get("total_mass_g"),
                "mass_map": mass_map,
            }
        )
    matches.sort(key=lambda item: (float(item["overlap_score"]), int(item["overlap_count"])), reverse=True)
    return matches[:NUTRITION5K_TOP_MATCHES]


def _apply_nutrition5k_mass_priors(
    ingredients_ranked: list[dict[str, Any]],
    nutrition5k_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    matches = _nutrition5k_overlap_matches(ingredients_ranked, nutrition5k_payload)
    summary: dict[str, Any] = {
        "requested": True,
        "matched_dish_count": len(matches),
        "applied_ingredient_count": 0,
        "top_matches": [
            {
                "dish_id": match["dish_id"],
                "overlap_score": match["overlap_score"],
                "overlap_count": match["overlap_count"],
                "overlap_ingredients": match["overlap_ingredients"][:10],
            }
            for match in matches[:5]
        ],
    }
    if len(matches) < NUTRITION5K_MIN_MATCHED_DISHES:
        return ingredients_ranked, summary

    weighted_mass_totals: dict[str, float] = defaultdict(float)
    score_totals: dict[str, float] = defaultdict(float)
    for match in matches:
        score = float(match["overlap_score"])
        for ingredient, mass_fraction in match["mass_map"].items():
            weighted_mass_totals[ingredient] += float(mass_fraction) * score
            score_totals[ingredient] += score

    mass_weights = {
        ingredient: weighted_mass_totals[ingredient] / score_totals[ingredient]
        for ingredient in weighted_mass_totals
        if score_totals[ingredient] > 0.0
    }

    adjusted: list[dict[str, Any]] = []
    applied_count = 0
    for record in ingredients_ranked:
        ingredient = normalize_ingredient(str(record.get("ingredient") or ""))
        original_weight = float(record.get("weight") or 0.0)
        updated = dict(record)
        updated["original_frequency_weight"] = round(original_weight, 6)
        updated["recipenlg_frequency_weight"] = round(original_weight, 6)
        updated["weight_source"] = "recipenlg_frequency"
        updated["mass_prior_source"] = "recipenlg_frequency"
        updated["nutrition5k_match_count"] = len(matches)
        if ingredient in mass_weights and mass_weights[ingredient] > 0.0:
            updated["weight"] = mass_weights[ingredient]
            updated["confidence"] = mass_weights[ingredient]
            updated["weight_source"] = "nutrition5k_mass_prior"
            updated["mass_prior_source"] = "nutrition5k_mass_prior"
            updated["mass_weight"] = round(mass_weights[ingredient], 6)
            applied_count += 1
        adjusted.append(updated)

    total = sum(float(item.get("weight") or 0.0) for item in adjusted)
    if total <= 0.0 or applied_count <= 0:
        return ingredients_ranked, summary
    for item in adjusted:
        normalized_weight = float(item.get("weight") or 0.0) / total
        item["weight"] = round(normalized_weight, 6)
        item["confidence"] = round(normalized_weight, 6)
    adjusted.sort(key=lambda item: (float(item["weight"]), int(item.get("source_count") or 0), str(item["ingredient"])), reverse=True)
    summary["applied_ingredient_count"] = applied_count
    return adjusted, summary


def _load_dish_mass_priors(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError("dish_mass_priors_artifact_missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    priors = payload.get("dish_mass_priors")
    if not isinstance(priors, dict):
        raise ValueError(f"Dish mass priors artifact has no dish_mass_priors object: {path}")
    return priors


def _missing_required_top_dish_ingredients(
    dish_predictions: list[dict[str, Any]],
    frequency_ranked: list[dict[str, Any]],
    mass_ranked: list[dict[str, Any]],
) -> list[str]:
    if not dish_predictions:
        return []
    top_label = str(dish_predictions[0].get("label") or "").strip()
    required = REQUIRED_TOP_DISH_INGREDIENTS.get(top_label)
    if not required:
        return []
    frequency_ingredients = {
        normalize_ingredient(str(item.get("ingredient") or "")) or ""
        for item in frequency_ranked
    }
    mass_ingredients = {
        normalize_ingredient(str(item.get("ingredient") or "")) or ""
        for item in mass_ranked
    }
    return sorted(ingredient for ingredient in required if ingredient in frequency_ingredients and ingredient not in mass_ingredients)


def _blend_dish_mass_priors(
    dish_predictions: list[dict[str, Any]],
    mass_priors: dict[str, Any],
    top_k_dishes: int,
    top_k_ingredients: int,
    allowed_ingredients: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    blended: dict[str, dict[str, Any]] = defaultdict(lambda: {"weight": 0.0, "source_count": 0, "dish_sources": [], "median_grams": []})
    missing_labels: list[str] = []
    low_coverage_labels: list[str] = []

    for prediction in dish_predictions[: max(1, top_k_dishes)]:
        label = str(prediction.get("label") or "").strip()
        confidence = _prediction_confidence(prediction)
        if not label or confidence <= 0.0:
            continue
        prior = mass_priors.get(label)
        if not prior:
            missing_labels.append(label)
            continue
        if int(prior.get("converted_recipe_count") or 0) < MIN_RECIPE_MATCHES:
            low_coverage_labels.append(label)
            continue
        for ingredient_record in prior.get("ingredients") or []:
            ingredient = normalize_ingredient(str(ingredient_record.get("ingredient") or ""))
            if not ingredient:
                continue
            if allowed_ingredients and ingredient not in allowed_ingredients:
                continue
            mass_fraction = float(ingredient_record.get("mass_fraction") or 0.0)
            if mass_fraction <= 0.0:
                continue
            bucket = blended[ingredient]
            bucket["weight"] += confidence * mass_fraction
            bucket["source_count"] += int(ingredient_record.get("source_count") or 0)
            bucket["median_grams"].append(float(ingredient_record.get("median_grams") or 0.0))
            bucket["dish_sources"].append(
                {
                    "label": label,
                    "confidence": confidence,
                    "mass_fraction": mass_fraction,
                    "coverage": prior.get("coverage"),
                    "converted_recipe_count": prior.get("converted_recipe_count"),
                }
            )

    total = sum(float(item["weight"]) for item in blended.values())
    if total <= 0.0:
        return [], {"missing_labels": missing_labels, "low_coverage_labels": low_coverage_labels, "applied_ingredient_count": 0}

    ranked = []
    for ingredient, payload in blended.items():
        weight = float(payload["weight"]) / total
        median_values = [value for value in payload["median_grams"] if value > 0.0]
        ranked.append(
            {
                "ingredient": ingredient,
                "confidence": round(weight, 6),
                "weight": round(weight, 6),
                "weight_source": "recipenlg_measurements_usda_portions",
                "mass_prior_source": "recipenlg_measurements_usda_portions",
                "visibility_label": "dish_mass_prior",
                "recipe_hits": int(payload["source_count"]),
                "source_count": int(payload["source_count"]),
                "mass_weight": round(weight, 6),
                "median_grams": round(sum(median_values) / len(median_values), 3) if median_values else None,
                "dish_sources": payload["dish_sources"],
            }
        )
    ranked.sort(key=lambda item: (float(item["weight"]), int(item.get("source_count") or 0), str(item["ingredient"])), reverse=True)
    if len(ranked) < DISH_MASS_MIN_APPLIED_INGREDIENTS:
        low_coverage_labels.append("insufficient_overlapping_ingredients")
        return [], {"missing_labels": missing_labels, "low_coverage_labels": low_coverage_labels, "applied_ingredient_count": 0}
    return ranked[: max(1, top_k_ingredients)], {
        "missing_labels": missing_labels,
        "low_coverage_labels": low_coverage_labels,
        "applied_ingredient_count": len(ranked),
    }


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_has_rows(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if row is None:
        return False
    count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return bool(count and int(count[0]) > 0)


def _pick_column(columns: set[str], *names: str) -> str | None:
    for name in names:
        if name in columns:
            return name
    return None


def _lookup_candidates(ingredient: str) -> list[tuple[str, str]]:
    normalized = normalize_ingredient(ingredient) or ingredient
    candidates: list[tuple[str, str]] = [(normalized, "exact")]
    source_row = CANONICAL_NUTRITION_ROW_SOURCES.get(normalized)
    if source_row:
        candidates.append((source_row, "alias"))
    for alias in LOCAL_NUTRITION_ALIASES.get(normalized, []):
        candidates.append((alias, "alias"))
    for alias in LOCAL_NUTRITION_ALIASES.get(ingredient, []):
        candidates.append((alias, "alias"))

    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate, method in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append((candidate, method))
    return deduped


def _lookup_candidates_with_db_aliases(connection: sqlite3.Connection, ingredient: str) -> list[tuple[str, str]]:
    candidates = _lookup_candidates(ingredient)
    if not _table_exists(connection, "ingredient_aliases"):
        return candidates
    alias_rows = connection.execute(
        """
        SELECT canonical_ingredient
        FROM ingredient_aliases
        WHERE LOWER(alias) = LOWER(?)
        """,
        (ingredient,),
    ).fetchall()
    for (canonical,) in alias_rows:
        canonical_text = str(canonical or "").strip()
        if canonical_text:
            candidates.insert(0, (canonical_text, "exact"))

    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate, method in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append((candidate, method))
    return deduped


def _is_sugar_like_ingredient(ingredient: str) -> bool:
    normalized = normalize_ingredient(ingredient) or ingredient.lower()
    return any(token in normalized for token in ("sugar", "syrup", "honey", "molasses", "glaze"))


def _coerce_calories_per_100g(value: float | None) -> tuple[float | None, bool]:
    if value is None:
        return None, False
    # Some rows appear to be stored in kJ while the column is labeled calories.
    if value > 900.0:
        return round(value / 4.184, 3), True
    return value, False


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _row_count(connection: sqlite3.Connection, table: str) -> int | None:
    if not _table_exists(connection, table):
        return None
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _lookup_sources(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if (
        _table_exists(connection, "canonical_food_aliases")
        and _table_exists(connection, "canonical_foods")
        and _table_exists(connection, "canonical_food_nutrients")
        and _table_has_rows(connection, "canonical_food_aliases")
        and _table_has_rows(connection, "canonical_food_nutrients")
    ):
        sources.append(
            {
                "label": "canonical_food_aliases+canonical_foods+canonical_food_nutrients",
                "from_sql": (
                    "canonical_food_aliases a "
                    "JOIN canonical_foods f ON a.canonical_food_id = f.canonical_food_id "
                    "JOIN canonical_food_nutrients n ON n.canonical_food_id = f.canonical_food_id"
                ),
                "name_columns": [
                    ("ingredient", "a.alias_name"),
                    ("canonical_name", "f.canonical_name"),
                ],
                "nutrient_columns": {
                    "calories": ("calories_per_100g", "n.calories_per_100g"),
                    "protein_g": ("protein_g_per_100g", "n.protein_g_per_100g"),
                    "total_fat_g": ("fat_g_per_100g", "n.fat_g_per_100g"),
                    "saturated_fat_g": ("saturated_fat_g_per_100g", "n.saturated_fat_g_per_100g"),
                    "total_carbs_g": ("carbs_g_per_100g", "n.carbs_g_per_100g"),
                    "fiber_g": ("fiber_g_per_100g", "n.fiber_g_per_100g"),
                    "total_sugars_g": ("sugar_g_per_100g", "n.sugar_g_per_100g"),
                    "sodium_mg": ("sodium_mg_per_100g", "n.sodium_mg_per_100g"),
                    "cholesterol_mg": ("cholesterol_mg_per_100g", "n.cholesterol_mg_per_100g"),
                    "source": ("preferred_source", "f.preferred_source"),
                    "fdc_id": ("source_fdc_id", "n.source_fdc_id"),
                },
                "strategy": "canonical_v2_alias_join",
            }
        )

    for table in ("generic_ingredient_nutrition", "ingredient_nutrients"):
        if not _table_exists(connection, table) or not _table_has_rows(connection, table):
            continue
        columns = _table_columns(connection, table)
        name_columns = [column for column in NAME_COLUMN_CANDIDATES if column in columns]
        nutrient_columns = {key: _pick_column(columns, *candidates) for key, candidates in NUTRIENT_COLUMN_CANDIDATES.items()}
        if not name_columns or not any(nutrient_columns.get(key) for key in NUTRIENT_KEYS):
            continue
        sources.append(
            {
                "label": table,
                "from_sql": table,
                "name_columns": [(column, column) for column in name_columns],
                "nutrient_columns": {key: (column, column) for key, column in nutrient_columns.items() if column},
                "strategy": "direct_table",
            }
        )

    if _table_exists(connection, "generic_foods") and _table_exists(connection, "generic_food_nutrition"):
        if _table_has_rows(connection, "generic_foods") and _table_has_rows(connection, "generic_food_nutrition"):
            food_columns = _table_columns(connection, "generic_foods")
            nutrition_columns = _table_columns(connection, "generic_food_nutrition")
            name_columns = [column for column in NAME_COLUMN_CANDIDATES if column in food_columns]
            nutrient_columns = {key: _pick_column(nutrition_columns, *candidates) for key, candidates in NUTRIENT_COLUMN_CANDIDATES.items()}
            if "source" in food_columns and not nutrient_columns.get("source"):
                nutrient_columns["source"] = "source"
            if name_columns and any(nutrient_columns.get(key) for key in NUTRIENT_KEYS):
                mapped_nutrients = {}
                for key, column in nutrient_columns.items():
                    if not column:
                        continue
                    prefix = "g" if column in food_columns else "n"
                    mapped_nutrients[key] = (column, f"{prefix}.{column}")
                sources.append(
                    {
                        "label": "generic_food_nutrition+generic_foods",
                        "from_sql": "generic_food_nutrition n JOIN generic_foods g ON n.food_id = g.food_id",
                        "name_columns": [(column, f"g.{column}") for column in name_columns],
                        "nutrient_columns": mapped_nutrients,
                        "strategy": "join_generic_foods",
                    }
                )
    return sources


def _lookup_environment(connection: sqlite3.Connection, db_path: Path | None = None) -> dict[str, Any]:
    tables = [str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    source_summaries = []
    for source in _lookup_sources(connection):
        source_summaries.append(
            {
                "table": source["label"],
                "name_columns": [column for column, _ in source["name_columns"]],
                "nutrient_columns": {key: column for key, (column, _) in source["nutrient_columns"].items()},
                "strategy": source["strategy"],
            }
        )
    return {
        "db_path": str(db_path or DB_PATH),
        "target_table_order": list(LOOKUP_TABLE_ORDER),
        "tables_present": tables,
        "lookup_sources": source_summaries,
        "matching_strategy": ["exact normalized", "alias/canonical", "simple LIKE", "lexical similarity"],
    }


def _select_row(connection: sqlite3.Connection, source: dict[str, Any], where_sql: str = "", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    select_parts = []
    for index, (column, expression) in enumerate(source["name_columns"]):
        select_parts.append(f"{expression} AS name_{index}")
    for key, (_, expression) in source["nutrient_columns"].items():
        select_parts.append(f"{expression} AS nutrient_{key}")
    sql = f"SELECT {', '.join(select_parts)} FROM {source['from_sql']}"
    if where_sql:
        sql += f" WHERE {where_sql}"
    rows = connection.execute(sql, params).fetchall()
    names = [description[0] for description in connection.execute(sql, params).description]
    return [dict(zip(names, row)) for row in rows]


def _row_has_nutrition(row: dict[str, Any]) -> bool:
    supported_keys = NUTRIENT_KEYS + FAT_COMPONENT_KEYS
    return any(_as_float(row.get(f"nutrient_{key}")) is not None for key in supported_keys)


def _best_name(row: dict[str, Any], source: dict[str, Any]) -> tuple[str | None, str | None]:
    for index, (column, _) in enumerate(source["name_columns"]):
        value = row.get(f"name_{index}")
        if value is not None and str(value).strip():
            return str(value).strip(), column
    return None, None


def _lookup_tokens(text: str) -> set[str]:
    return {token for token in re.sub(r"[^a-z ]+", " ", str(text).lower()).split() if token}


def _load_allowed_usda_fdc_ids(food_csv_path: Path = USDA_FOOD_CSV_PATH) -> set[int] | None:
    if not food_csv_path.exists():
        return None
    allowed: set[int] = set()
    with food_csv_path.open("r", encoding="utf-8", newline="", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            data_type = str(row.get("data_type") or "").strip().lower()
            if data_type not in ALLOWED_USDA_DATA_TYPES:
                continue
            try:
                fdc_id = int(str(row.get("fdc_id") or "").strip())
            except ValueError:
                continue
            allowed.add(fdc_id)
    return allowed


_ALLOWED_USDA_FDC_IDS_CACHE: set[int] | None | bool = False


def _allowed_usda_fdc_ids() -> set[int] | None:
    global _ALLOWED_USDA_FDC_IDS_CACHE
    if _ALLOWED_USDA_FDC_IDS_CACHE is False:
        _ALLOWED_USDA_FDC_IDS_CACHE = _load_allowed_usda_fdc_ids()
    if _ALLOWED_USDA_FDC_IDS_CACHE is None:
        return None
    return _ALLOWED_USDA_FDC_IDS_CACHE


def _category_compatible(original: str, candidate: str) -> bool:
    query_tokens = _lookup_tokens(original)
    candidate_tokens = _lookup_tokens(candidate)
    query_is_oil = bool(query_tokens & OIL_TOKENS)
    query_is_milk = bool(query_tokens & MILK_TOKENS)
    query_is_cheese = bool(query_tokens & CHEESE_TOKENS)
    if query_is_oil and not (candidate_tokens & OIL_TOKENS):
        return False
    if query_is_milk and not (candidate_tokens & MILK_TOKENS):
        return False
    if query_is_cheese and not (candidate_tokens & CHEESE_TOKENS):
        return False
    return True


def _row_to_nutrition_result(
    row: dict[str, Any],
    source: dict[str, Any],
    ingredient: str,
    normalized: str,
    match_method: str,
    match_score: float | None,
    matched_query: str,
    enforce_usda_data_type: bool = True,
) -> tuple[IngredientNutrition | None, dict[str, Any]]:
    matched_ingredient, matched_column = _best_name(row, source)
    if not matched_ingredient or not _row_has_nutrition(row):
        return None, {}
    if not _category_compatible(normalized, matched_ingredient):
        return None, {"reject_reason": "category_mismatch"}
    raw_fdc_id = row.get("nutrient_fdc_id")
    fdc_id_value: int | None = None
    try:
        if raw_fdc_id is not None:
            fdc_id_value = int(raw_fdc_id)
    except (TypeError, ValueError):
        fdc_id_value = None
    allowed_fdc_ids = _allowed_usda_fdc_ids() if enforce_usda_data_type else None
    if allowed_fdc_ids is not None and fdc_id_value is not None and fdc_id_value not in allowed_fdc_ids:
        return None, {"reject_reason": "unsupported_usda_data_type"}
    calories_raw = _as_float(row.get("nutrient_calories"))
    calories_value, calories_converted = _coerce_calories_per_100g(calories_raw)
    protein_value = _as_float(row.get("nutrient_protein_g"))
    total_fat_value = _as_float(row.get("nutrient_total_fat_g"))
    fat_components = {
        "saturated_fat_g": _as_float(row.get("nutrient_saturated_fat_g")),
        "monounsaturated_fat_g": _as_float(row.get("nutrient_monounsaturated_fat_g")),
        "polyunsaturated_fat_g": _as_float(row.get("nutrient_polyunsaturated_fat_g")),
        "trans_fat_g": _as_float(row.get("nutrient_trans_fat_g")),
    }
    fat_component_sum = sum(value for value in fat_components.values() if value is not None)
    total_fat_fallback_applied = False
    if (total_fat_value is None or total_fat_value <= 0.0) and fat_component_sum > 0.0:
        total_fat_value = round(fat_component_sum, 3)
        total_fat_fallback_applied = True
    total_carb_value = _as_float(row.get("nutrient_total_carbs_g"))
    total_sugar_value = _as_float(row.get("nutrient_total_sugars_g"))
    sugar_fallback_applied = False
    if total_sugar_value is None and total_carb_value is not None and _is_sugar_like_ingredient(matched_ingredient):
        total_sugar_value = total_carb_value
        sugar_fallback_applied = True
    if calories_value is None and any(value is not None for value in (protein_value, total_fat_value, total_carb_value)):
        calories_value = round((protein_value or 0.0) * 4.0 + (total_fat_value or 0.0) * 9.0 + (total_carb_value or 0.0) * 4.0, 3)
    source_value = row.get("nutrient_source")
    public_match_method = "existing_similarity" if match_method in {"like", "similarity"} else match_method
    debug = {
        "ingredient": ingredient,
        "original_ingredient": ingredient,
        "normalized": normalized,
        "normalized_ingredient": normalized,
        "db_table": source["label"],
        "matched_table": source["label"],
        "matched_column": matched_column,
        "aliases_tried": [],
        "alias_candidates_tried": [],
        "exact_match": matched_query if public_match_method == "exact" else None,
        "similarity_match": matched_ingredient if public_match_method == "existing_similarity" else None,
        "matched_ingredient": matched_ingredient,
        "match_method": public_match_method,
        "match_score": match_score,
        "fdc_id": fdc_id_value if fdc_id_value is not None else raw_fdc_id,
        "source": str(source_value) if source_value is not None else None,
        "status": "found",
        "reason": None,
        "lookup_strategy": source["strategy"],
        "sql_strategy": "parameterized exact/like/select scan",
        "usda_data_type_filter": sorted(ALLOWED_USDA_DATA_TYPES) if enforce_usda_data_type else "disabled_for_nondefault_db",
        "calories_unit_adjustment": "kj_to_kcal" if calories_converted else None,
        "total_fat_fallback_applied": total_fat_fallback_applied,
        "fat_components_g": fat_components,
        "sugar_fallback_applied": sugar_fallback_applied,
        "nutrition_per_100g": {
            "calories": calories_value,
            "protein_g": protein_value,
            "total_fat_g": total_fat_value,
            "total_carbs_g": total_carb_value,
            "fiber_g": _as_float(row.get("nutrient_fiber_g")),
            "total_sugars_g": total_sugar_value,
            "sodium_mg": _as_float(row.get("nutrient_sodium_mg")),
            "cholesterol_mg": _as_float(row.get("nutrient_cholesterol_mg")),
        },
    }
    return IngredientNutrition(
        ingredient=matched_ingredient,
        calories=calories_value,
        protein_g=protein_value,
        total_fat_g=total_fat_value,
        total_carbs_g=total_carb_value,
        fiber_g=_as_float(row.get("nutrient_fiber_g")),
        total_sugars_g=total_sugar_value,
        sodium_mg=_as_float(row.get("nutrient_sodium_mg")),
        cholesterol_mg=_as_float(row.get("nutrient_cholesterol_mg")),
        source=str(source_value) if source_value is not None else None,
        fdc_id=fdc_id_value if fdc_id_value is not None else raw_fdc_id,
    ), debug


def _empty_lookup_debug(connection: sqlite3.Connection, ingredient: str, db_path: Path | None, reason: str) -> dict[str, Any]:
    normalized = normalize_ingredient(ingredient) or ingredient
    environment = _lookup_environment(connection, db_path)
    return {
        "ingredient": ingredient,
        "original_ingredient": ingredient,
        "normalized": normalized,
        "normalized_ingredient": normalized,
        "db_path": str(db_path or DB_PATH),
        "db_table": None,
        "matched_table": None,
        "matched_column": None,
        "aliases_tried": [],
        "alias_candidates_tried": [],
        "exact_match": None,
        "similarity_match": None,
        "matched_ingredient": None,
        "match_method": "not_found",
        "match_score": None,
        "status": "not_found",
        "reason": reason,
        "lookup_environment": environment,
    }


def _nearest_candidates(connection: sqlite3.Connection, ingredient: str, limit: int = 5) -> list[str]:
    candidates = _lookup_candidates(ingredient)
    scored: list[tuple[float, str]] = []
    for source in _lookup_sources(connection):
        for row in _select_row(connection, source):
            if not _row_has_nutrition(row):
                continue
            matched_name, _ = _best_name(row, source)
            if not matched_name:
                continue
            if lexical_match_score is not None:
                score = max(lexical_match_score(query, matched_name) for query, _ in candidates)
            else:
                query_tokens = set().union(*(_lookup_tokens(query) for query, _ in candidates))
                name_tokens = _lookup_tokens(matched_name)
                score = len(query_tokens & name_tokens) / max(1, len(query_tokens | name_tokens))
            scored.append((float(score), matched_name))
    deduped: dict[str, float] = {}
    for score, name in scored:
        deduped[name] = max(score, deduped.get(name, 0.0))
    return [
        name
        for name, _ in sorted(deduped.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def _uses_default_palm_db(db_path: Path | None) -> bool:
    if db_path is None:
        return True
    try:
        return Path(db_path).resolve() == Path(DB_PATH).resolve()
    except OSError:
        return Path(db_path) == Path(DB_PATH)


def _lookup_nutrition(
    connection: sqlite3.Connection,
    ingredient: str,
    db_path: Path | None = None,
) -> tuple[IngredientNutrition | None, dict[str, Any]]:
    normalized = normalize_ingredient(ingredient) or ingredient
    sources = _lookup_sources(connection)
    if not sources:
        return None, _empty_lookup_debug(connection, ingredient, db_path, "no_supported_ingredient_nutrition_table")

    candidates = _lookup_candidates_with_db_aliases(connection, ingredient)
    aliases_tried = [query for query, method in candidates if method == "alias"]
    exact_row_missing_nutrients = False
    enforce_usda_filter = _uses_default_palm_db(db_path)
    for query, method in candidates:
        for source in sources:
            where = " OR ".join(f"LOWER({expression}) = LOWER(?)" for _, expression in source["name_columns"])
            rows = _select_row(connection, source, where, tuple(query for _ in source["name_columns"]))
            for row in rows:
                if not _row_has_nutrition(row):
                    if method == "exact" and query == normalized:
                        exact_row_missing_nutrients = True
                    continue
                nutrition, debug = _row_to_nutrition_result(
                    row,
                    source,
                    ingredient,
                    normalized,
                    method,
                    1.0,
                    query,
                    enforce_usda_data_type=enforce_usda_filter,
                )
                if nutrition is not None:
                    debug["aliases_tried"] = aliases_tried
                    debug["alias_candidates_tried"] = aliases_tried
                    return nutrition, debug

    if exact_row_missing_nutrients:
        debug = _empty_lookup_debug(connection, ingredient, db_path, "matched_row_missing_supported_nutrients")
        debug["matched_ingredient"] = normalized
        debug["matched_table"] = "generic_ingredient_nutrition"
        debug["matched_column"] = "ingredient"
        debug["aliases_tried"] = aliases_tried
        debug["alias_candidates_tried"] = aliases_tried
        return None, debug

    for query, _ in candidates:
        pattern = f"%{query}%"
        for source in sources:
            where = " OR ".join(f"LOWER({expression}) LIKE LOWER(?)" for _, expression in source["name_columns"])
            rows = _select_row(connection, source, where, tuple(pattern for _ in source["name_columns"]))
            rows.sort(key=lambda item: min(len(str(value or "")) for key, value in item.items() if key.startswith("name_")))
            for row in rows:
                matched_name, _ = _best_name(row, source)
                if not matched_name or not _lookup_tokens(query).issubset(_lookup_tokens(matched_name)):
                    continue
                nutrition, debug = _row_to_nutrition_result(
                    row,
                    source,
                    ingredient,
                    normalized,
                    "like",
                    0.75,
                    query,
                    enforce_usda_data_type=enforce_usda_filter,
                )
                if nutrition is not None:
                    debug["aliases_tried"] = aliases_tried
                    debug["alias_candidates_tried"] = aliases_tried
                    return nutrition, debug

    if lexical_match_score is not None:
        best: tuple[float, dict[str, Any], dict[str, Any], str] | None = None
        for source in sources:
            for row in _select_row(connection, source):
                if not _row_has_nutrition(row):
                    continue
                matched_name, _ = _best_name(row, source)
                if not matched_name:
                    continue
                score = max(lexical_match_score(query, matched_name) for query, _ in candidates)
                if best is None or score > best[0]:
                    best = (score, row, source, matched_name)
        if best is not None and best[0] >= 1.5:
            nutrition, debug = _row_to_nutrition_result(
                best[1],
                best[2],
                ingredient,
                normalized,
                "similarity",
                round(best[0], 3),
                normalized,
                enforce_usda_data_type=enforce_usda_filter,
            )
            if nutrition is not None:
                debug["aliases_tried"] = aliases_tried
                debug["alias_candidates_tried"] = aliases_tried
                return nutrition, debug

    debug = _empty_lookup_debug(connection, ingredient, db_path, "no nearby DB row found")
    debug["aliases_tried"] = aliases_tried
    debug["alias_candidates_tried"] = aliases_tried
    debug["nearest_candidates"] = _nearest_candidates(connection, ingredient)
    return None, debug


def lookup_ingredient_nutrition(ingredient: str, db_path: str | Path | None = None) -> dict[str, Any]:
    database_path = resolve_runtime_db_path(db_path)
    if not database_path.exists():
        return {
            "status": "not_found",
            "original_ingredient": ingredient,
            "normalized_ingredient": normalize_ingredient(ingredient) or ingredient,
            "matched_ingredient": None,
            "matched_table": None,
            "matched_column": None,
            "alias_candidates_tried": [],
            "aliases_tried": [],
            "match_method": "not_found",
            "match_score": None,
            "fdc_id": None,
            "nutrition_per_100g": None,
            "reason": "db_path_missing",
            "db_path": str(database_path),
        }
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        nutrition, debug = _lookup_nutrition(connection, ingredient, database_path)
    return {
        "status": debug.get("status"),
        "original_ingredient": ingredient,
        "normalized_ingredient": debug.get("normalized_ingredient") or debug.get("normalized"),
        "matched_ingredient": debug.get("matched_ingredient"),
        "matched_table": debug.get("matched_table") or debug.get("db_table"),
        "matched_column": debug.get("matched_column"),
        "alias_candidates_tried": debug.get("alias_candidates_tried") or debug.get("aliases_tried") or [],
        "aliases_tried": debug.get("aliases_tried") or [],
        "match_method": debug.get("match_method"),
        "match_score": debug.get("match_score"),
        "fdc_id": debug.get("fdc_id"),
        "nutrition_per_100g": debug.get("nutrition_per_100g") if nutrition is not None else None,
        "reason": debug.get("reason"),
        "nearest_candidates": debug.get("nearest_candidates") or [],
        "debug": debug,
    }


def ensure_ingredient_alias_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ingredient_aliases (
            alias TEXT PRIMARY KEY,
            canonical_ingredient TEXT NOT NULL,
            notes TEXT
        )
        """
    )


def seed_ingredient_alias_backbone(db_path: str | Path = DB_PATH) -> dict[str, Any]:
    database_path = Path(db_path)
    result: dict[str, Any] = {
        "db_path": str(database_path),
        "inserted_or_updated_count": 0,
        "alias_count": 0,
    }
    if not database_path.exists():
        result["error"] = "db_path_missing"
        return result

    rows = [
        (
            alias,
            canonical,
            "PALM canonical ingredient backbone",
        )
        for alias, canonical in sorted(CANONICAL_INGREDIENT_ALIASES.items())
        if alias != canonical
    ]
    with closing(sqlite3.connect(database_path)) as connection:
        ensure_ingredient_alias_schema(connection)
        connection.executemany(
            """
            INSERT INTO ingredient_aliases (alias, canonical_ingredient, notes)
            VALUES (?, ?, ?)
            ON CONFLICT(alias) DO UPDATE SET
                canonical_ingredient = excluded.canonical_ingredient,
                notes = excluded.notes
            """,
            rows,
        )
        connection.commit()
    result["inserted_or_updated_count"] = len(rows)
    result["alias_count"] = len(rows)
    return result


def seed_common_ingredient_nutrition_alias_rows(db_path: str | Path = DB_PATH) -> dict[str, Any]:
    database_path = Path(db_path)
    result: dict[str, Any] = {
        "db_path": str(database_path),
        "inserted_count": 0,
        "skipped_existing_count": 0,
        "missing_source_count": 0,
        "inserted": [],
        "skipped_existing": [],
        "missing_sources": [],
    }
    if not database_path.exists():
        result["error"] = "db_path_missing"
        return result

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        if not _table_exists(connection, "generic_ingredient_nutrition"):
            result["error"] = "generic_ingredient_nutrition_missing"
            return result
        columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(generic_ingredient_nutrition)").fetchall()]
        if "ingredient" not in columns:
            result["error"] = "ingredient_column_missing"
            return result

        for target, source in _common_ingredient_nutrition_alias_rows():
            existing = connection.execute(
                "SELECT ingredient FROM generic_ingredient_nutrition WHERE LOWER(ingredient) = LOWER(?)",
                (target,),
            ).fetchone()
            if existing is not None:
                result["skipped_existing_count"] += 1
                result["skipped_existing"].append(target)
                continue

            source_row = connection.execute(
                "SELECT * FROM generic_ingredient_nutrition WHERE LOWER(ingredient) = LOWER(?)",
                (source,),
            ).fetchone()
            if source_row is None:
                result["missing_source_count"] += 1
                result["missing_sources"].append({"target": target, "source": source})
                continue

            payload = {column: source_row[column] for column in columns}
            payload["ingredient"] = target
            if "source" in payload:
                payload["source"] = f"usda_generic_alias:{source}"
            if "description" in payload and not payload.get("description"):
                payload["description"] = f"Alias copy of {source}"
            insert_columns = list(payload)
            placeholders = ", ".join("?" for _ in insert_columns)
            quoted_columns = ", ".join(insert_columns)
            connection.execute(
                f"INSERT INTO generic_ingredient_nutrition ({quoted_columns}) VALUES ({placeholders})",
                tuple(payload[column] for column in insert_columns),
            )
            result["inserted_count"] += 1
            result["inserted"].append({"target": target, "source": source, "fdc_id": payload.get("fdc_id")})
        connection.commit()
    return result


def cleanup_noncanonical_ingredient_nutrition_alias_rows(db_path: str | Path = DB_PATH) -> dict[str, Any]:
    database_path = Path(db_path)
    result: dict[str, Any] = {
        "db_path": str(database_path),
        "deleted_count": 0,
        "deleted": [],
    }
    if not database_path.exists():
        result["error"] = "db_path_missing"
        return result

    with closing(sqlite3.connect(database_path)) as connection:
        if not _table_exists(connection, "generic_ingredient_nutrition"):
            result["error"] = "generic_ingredient_nutrition_missing"
            return result
        rows = connection.execute(
            """
            SELECT ingredient, source
            FROM generic_ingredient_nutrition
            WHERE source LIKE 'usda_generic_alias:%'
            ORDER BY ingredient
            """
        ).fetchall()
        to_delete = []
        for ingredient, source in rows:
            ingredient_text = str(ingredient or "").strip()
            canonical = normalize_ingredient(ingredient_text) or ingredient_text
            if canonical != ingredient_text:
                to_delete.append((ingredient_text, str(source or "")))
        connection.executemany(
            "DELETE FROM generic_ingredient_nutrition WHERE ingredient = ?",
            [(ingredient,) for ingredient, _ in to_delete],
        )
        connection.commit()
    result["deleted_count"] = len(to_delete)
    result["deleted"] = [{"ingredient": ingredient, "source": source} for ingredient, source in to_delete]
    return result


def audit_nutrition_db(db_path: str | Path = DB_PATH) -> dict[str, Any]:
    database_path = Path(db_path)
    report: dict[str, Any] = {
        "db_path": str(database_path),
        "db_exists": database_path.exists(),
        "tables_present": [],
        "tables": {},
        "ingredient_level_source": None,
        "lookup_environment": None,
    }
    if not database_path.exists():
        return report
    with closing(sqlite3.connect(database_path)) as connection:
        tables = [str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        report["tables_present"] = tables
        for table in AUDIT_TABLES:
            exists = table in tables
            report["tables"][table] = {
                "exists": exists,
                "row_count": _row_count(connection, table) if exists else None,
                "columns": sorted(_table_columns(connection, table)) if exists else [],
            }
        environment = _lookup_environment(connection, database_path)
        report["lookup_environment"] = environment
        sources = environment.get("lookup_sources") or []
        report["ingredient_level_source"] = sources[0]["table"] if sources else None
    return report


def _lookup_support_warnings(connection: sqlite3.Connection) -> list[str]:
    warnings: list[str] = []
    populated_tables = [table for table in AUDIT_TABLES if _table_exists(connection, table) and _table_has_rows(connection, table)]
    if not populated_tables:
        warnings.append("ingredient_table_empty")
    sources = _lookup_sources(connection)
    if not sources:
        warnings.append("no_supported_ingredient_nutrition_table")
        has_name_columns = False
        has_nutrient_columns = False
        for table in populated_tables:
            columns = _table_columns(connection, table)
            has_name_columns = has_name_columns or any(column in columns for column in NAME_COLUMN_CANDIDATES)
            has_nutrient_columns = has_nutrient_columns or any(
                any(candidate in columns for candidate in candidates)
                for key, candidates in NUTRIENT_COLUMN_CANDIDATES.items()
                if key in NUTRIENT_KEYS
            )
        if not has_name_columns:
            warnings.append("no_supported_name_columns")
        if not has_nutrient_columns:
            warnings.append("no_supported_nutrient_columns")
    return warnings


def _aggregate_nutrition(matched: list[dict[str, Any]]) -> dict[str, float | None]:
    if any(record.get("contribution_basis") == "ingredient_grams" for record in matched):
        return _aggregate_nutrition_from_grams(matched)
    totals = {key: 0.0 for key in NUTRIENT_KEYS}
    included = [record for record in matched if record.get("included_in_nutrition", True)]
    representatives = _representative_group_indexes(matched, grams_basis=False)
    selected = [matched[index] for index in representatives.values()]
    total_weight = sum(_record_calc_weight(record) for record in selected)
    if total_weight <= 0.0:
        return {key: None for key in NUTRIENT_KEYS}
    for record in selected:
        weight = _record_calc_weight(record)
        nutrition = record.get("nutrition_per_100g") or {}
        for key in NUTRIENT_KEYS:
            value = _as_float(nutrition.get(key))
            if value is None:
                continue
            totals[key] += value * weight
    return {key: round(totals[key] / total_weight, 3) for key in NUTRIENT_KEYS}


def _nutrition_collapse_key(record: dict[str, Any]) -> str:
    fdc_id = record.get("fdc_id")
    if fdc_id not in {None, ""}:
        return f"fdc:{fdc_id}"
    table = str(record.get("matched_table") or record.get("db_table") or "usda").strip().lower()
    ingredient = normalize_ingredient(str(record.get("matched_ingredient") or record.get("normalized") or record.get("ingredient") or ""))
    return f"{table}:{ingredient}"


def _record_calc_grams(record: dict[str, Any]) -> float:
    return float(
        record.get("grams_per_100g_dish")
        or record.get("original_grams_per_100g_dish")
        or record.get("grams_used_for_nutrition")
        or record.get("grams_default")
        or 0.0
    )


def _record_calc_weight(record: dict[str, Any]) -> float:
    return float(record.get("weight") or 0.0)


def _representative_group_indexes(records: list[dict[str, Any]], *, grams_basis: bool) -> dict[str, int]:
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, record in enumerate(records):
        if not record.get("included_in_nutrition", True):
            continue
        groups[_nutrition_collapse_key(record)].append((index, record))
    representatives: dict[str, int] = {}
    for key, items in groups.items():
        representatives[key] = max(
            items,
            key=lambda item: (
                _record_calc_grams(item[1]) if grams_basis else _record_calc_weight(item[1]),
                float(item[1].get("confidence") or 0.0),
                -int(item[1].get("ingredient_rank") or 999999),
            ),
        )[0]
    return representatives


def _aggregate_nutrition_from_grams(matched: list[dict[str, Any]]) -> dict[str, float | None]:
    totals = {key: 0.0 for key in NUTRIENT_KEYS}
    included = [record for record in matched if record.get("included_in_nutrition", True)]
    if not included:
        return {key: None for key in NUTRIENT_KEYS}
    renormalize = any(record.get("renormalize_grams_to_100g") for record in matched)
    representatives = _representative_group_indexes(matched, grams_basis=True)
    selected = [matched[index] for index in representatives.values()]
    included_grams_total = sum(_record_calc_grams(record) for record in selected)
    has_any_value = False
    for record in selected:
        grams = _record_calc_grams(record)
        if renormalize and included_grams_total > 0.0:
            grams = grams / included_grams_total * 100.0
        if grams <= 0.0:
            continue
        nutrition = record.get("nutrition_per_100g") or {}
        for key in NUTRIENT_KEYS:
            value = _as_float(nutrition.get(key))
            if value is None:
                continue
            has_any_value = True
            totals[key] += value * grams / 100.0
    if not has_any_value:
        return {key: None for key in NUTRIENT_KEYS}
    return {key: round(totals[key], 3) for key in NUTRIENT_KEYS}


def _contribution_row(record: dict[str, Any]) -> dict[str, Any]:
    normalized_weight = float(record.get("normalized_weight_used", record.get("weight") or 0.0) or 0.0)
    contributions = record.get("nutrient_contributions") or {}
    return {
        "ingredient": record.get("ingredient"),
        "normalized": record.get("normalized"),
        "matched_ingredient": record.get("matched_ingredient"),
        "match_method": record.get("match_method"),
        "weight": round(float(record.get("weight") or 0.0), 6),
        "normalized_weight_used": round(normalized_weight, 6),
        "weight_source": record.get("weight_source"),
        "original_frequency_weight": record.get("original_frequency_weight"),
        "mass_weight": record.get("mass_weight"),
        "nutrition5k_match_count": record.get("nutrition5k_match_count"),
        "nutrition_source": record.get("nutrition_source"),
        "grams_default": record.get("grams_default"),
        "original_grams_per_100g_dish": record.get("original_grams_per_100g_dish", record.get("grams_per_100g_dish")),
        "grams_per_100g_dish": record.get("grams_per_100g_dish"),
        "adjusted_grams_after_exclusions": record.get("adjusted_grams_after_exclusions"),
        "renormalization_factor": record.get("renormalization_factor"),
        "included": record.get("included_in_nutrition", True),
        "portion_method": record.get("portion_method"),
        "nutrition_collapse_key": record.get("nutrition_collapse_key"),
        "nutrition_group_size": record.get("nutrition_group_size"),
        "nutrition_group_representative": record.get("nutrition_group_representative"),
        "nutrition_collapsed_into": record.get("nutrition_collapsed_into"),
        **{f"{key}_contrib": contributions.get(f"{key}_contrib") for key in NUTRIENT_KEYS},
        "calories_contribution": contributions.get("calories_contrib"),
        "protein_contribution": contributions.get("protein_g_contrib"),
        "fat_contribution": contributions.get("total_fat_g_contrib"),
        "carbs_contribution": contributions.get("total_carbs_g_contrib"),
        "sugar_contribution": contributions.get("total_sugars_g_contrib"),
        "sodium_contribution": contributions.get("sodium_mg_contrib"),
    }


def _with_nutrition_contributions(matched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grams_basis = any(record.get("contribution_basis") == "ingredient_grams" for record in matched)
    representatives = _representative_group_indexes(matched, grams_basis=grams_basis)
    representative_indexes = set(representatives.values())
    group_sizes = Counter(_nutrition_collapse_key(record) for record in matched if record.get("included_in_nutrition", True))
    total_weight = sum(_record_calc_weight(matched[index]) for index in representative_indexes)
    included_grams_total = sum(_record_calc_grams(matched[index]) for index in representative_indexes)
    renormalize_grams = any(record.get("renormalize_grams_to_100g") for record in matched)
    renormalization_factor = (100.0 / included_grams_total) if renormalize_grams and included_grams_total > 0.0 else 1.0
    annotated: list[dict[str, Any]] = []
    for index, record in enumerate(matched):
        item = dict(record)
        included = bool(item.get("included_in_nutrition", True))
        collapse_key = _nutrition_collapse_key(item)
        selected = included and index in representative_indexes
        original_grams = _record_calc_grams(item)
        if item.get("contribution_basis") == "ingredient_grams":
            grams = original_grams if selected else 0.0
            if renormalize_grams and included_grams_total > 0.0:
                grams = grams / included_grams_total * 100.0
            normalized_weight = grams / 100.0 if included and grams > 0.0 else 0.0
        else:
            normalized_weight = _record_calc_weight(item) / total_weight if selected and total_weight > 0.0 else 0.0
            grams = original_grams
        item["included_in_nutrition"] = included
        item["nutrition_collapse_key"] = collapse_key
        item["nutrition_group_size"] = int(group_sizes.get(collapse_key) or 0)
        item["nutrition_group_representative"] = bool(selected)
        item["original_grams_per_100g_dish"] = round(original_grams, 6) if original_grams else 0.0
        item["adjusted_grams_after_exclusions"] = round(grams, 6) if included and selected else 0.0
        item["renormalization_factor"] = round(renormalization_factor, 6)
        if included and not selected and group_sizes.get(collapse_key, 0) > 1:
            representative = matched[representatives[collapse_key]]
            item["nutrition_collapsed_into"] = representative.get("matched_ingredient") or representative.get("ingredient")
        item["normalized_weight_used"] = round(normalized_weight, 6)
        nutrition = item.get("nutrition_per_100g") or {}
        contributions: dict[str, float | None] = {}
        for key in NUTRIENT_KEYS:
            value = _as_float(nutrition.get(key))
            contributions[f"{key}_contrib"] = (
                _round_float(value * normalized_weight, 3)
                if value is not None and normalized_weight > 0.0
                else 0.0
            )
        item["nutrient_contributions"] = contributions
        item["nutrition_contribution_per_100g"] = _contribution_row(item)
        annotated.append(item)
    return annotated


def _contribution_sum_warnings(estimated: dict[str, Any] | None, matched: list[dict[str, Any]]) -> list[str]:
    if not estimated:
        return []
    for key in NUTRIENT_KEYS:
        expected = _as_float(estimated.get(key))
        if expected is None:
            continue
        actual = sum(
            _as_float((record.get("nutrient_contributions") or {}).get(f"{key}_contrib")) or 0.0
            for record in matched
        )
        if abs(actual - expected) > 0.05:
            return ["nutrition_contribution_sum_mismatch"]
    return []


def _ingredient_exclusion_key(record: dict[str, Any] | str) -> str:
    if isinstance(record, str):
        value = record
    else:
        value = (
            record.get("normalized")
            or record.get("normalized_ingredient")
            or record.get("matched_ingredient")
            or record.get("canonical")
            or record.get("raw_ingredient")
            or record.get("ingredient")
            or ""
        )
    return normalize_ingredient(str(value)) or str(value).strip().lower()


def _mark_inclusion(record: dict[str, Any], excluded_keys: set[str]) -> dict[str, Any]:
    item = dict(record)
    key = _ingredient_exclusion_key(item)
    excluded = bool(key and key in excluded_keys)
    item["included_in_nutrition"] = not excluded
    item["exclusion_reason"] = "user_excluded" if excluded else None
    return item


def recalculate_bridge_nutrition(
    bridge_result: dict[str, Any],
    excluded_ingredients: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Recalculate bridge nutrition from existing matched ingredient rows.

    This intentionally does not rerun dish prediction, ingredient prior blending, or
    USDA lookup. It reuses the current per-ingredient nutrition payload and only
    changes which matched ingredients contribute to the aggregate.
    """
    result = copy.deepcopy(bridge_result)
    excluded_keys = {
        key
        for key in (_ingredient_exclusion_key(ingredient) for ingredient in (excluded_ingredients or []))
        if key
    }

    ranked = [_mark_inclusion(item, excluded_keys) for item in result.get("ingredients_ranked") or []]
    result["ingredients_ranked"] = ranked
    result["visible_ingredients"] = [_mark_inclusion(item, excluded_keys) for item in result.get("visible_ingredients") or ranked]

    palm_ready = dict(result.get("palm_ready") or {})
    marked_matched = [_mark_inclusion(item, excluded_keys) for item in palm_ready.get("matched_ingredients") or []]
    if excluded_keys and palm_ready.get("nutrition_basis") == "dish_ingredient_priors_grams":
        marked_matched = [{**item, "renormalize_grams_to_100g": True} for item in marked_matched]
    matched = _with_nutrition_contributions(marked_matched)
    unmatched = [_mark_inclusion(item, excluded_keys) for item in palm_ready.get("unmatched_ingredients") or []]
    included_matched = [item for item in matched if item.get("included_in_nutrition")]
    excluded_count = len([item for item in matched + unmatched if not item.get("included_in_nutrition")])

    normalization = copy.deepcopy(result.get("ingredient_normalization") or {})
    normalization["matched"] = [_mark_inclusion(item, excluded_keys) for item in normalization.get("matched") or []]
    normalization["unmatched"] = [_mark_inclusion(item, excluded_keys) for item in normalization.get("unmatched") or []]
    result["ingredient_normalization"] = normalization

    recalc_metadata = {
        "user_exclusions_applied": bool(excluded_keys),
        "excluded_ingredients": sorted(excluded_keys),
        "included_count": len(included_matched),
        "excluded_count": excluded_count,
        "renormalized": bool(excluded_keys),
    }
    palm_ready["nutrition_recalc"] = recalc_metadata
    palm_ready["excluded_ingredients"] = sorted(excluded_keys)
    palm_ready["matched_ingredients"] = matched
    palm_ready["unmatched_ingredients"] = unmatched
    collapse_groups = Counter(
        item.get("nutrition_collapse_key")
        for item in matched
        if item.get("included_in_nutrition", True) and item.get("nutrition_collapse_key")
    )
    palm_ready["nutrition_collapse_summary"] = {
        "basis": "fdc_id_or_usda_match",
        "input_ingredient_rows": len([item for item in matched if item.get("included_in_nutrition", True)]),
        "nutrition_source_rows": len(collapse_groups),
        "collapsed_group_count": len([count for count in collapse_groups.values() if count > 1]),
    }

    warnings = list(result.get("warnings") or [])
    if not included_matched:
        palm_ready["status"] = "pending"
        palm_ready["estimated_nutrition_per_100g"] = None
        palm_ready["nutrition_composition"] = []
        warnings.append("no_included_found_ingredients_for_nutrition")
    else:
        palm_ready["status"] = "ready"
        palm_ready["estimated_nutrition_per_100g"] = _aggregate_nutrition(matched)
        palm_ready["nutrition_composition"] = [_contribution_row(item) for item in included_matched]
        warnings.extend(_contribution_sum_warnings(palm_ready["estimated_nutrition_per_100g"], matched))
        if excluded_keys:
            if palm_ready.get("nutrition_basis") == "dish_ingredient_priors_grams":
                palm_ready["label"] = "Estimated nutrition from selected DB-backed ingredients only"
            else:
                palm_ready["label"] = "Estimated nutrition per 100g from selected ingredients only"
            if len(included_matched) / max(1, len(matched)) < 0.5:
                warnings.append("ingredient_lookup_low_coverage_after_user_exclusions")

    result["palm_ready"] = palm_ready
    result["nutrition_recalc"] = recalc_metadata
    result["warnings"] = list(dict.fromkeys(warnings))
    return result


def _outlier_warnings(dish_label: str | None, estimated: dict[str, float | None] | None) -> list[str]:
    if not estimated:
        return []
    warnings: list[str] = []
    sodium = _as_float(estimated.get("sodium_mg"))
    sugars = _as_float(estimated.get("total_sugars_g"))
    calories = _as_float(estimated.get("calories"))
    label = str(dish_label or "").strip().lower()
    if sodium is not None and sodium > 1500.0:
        warnings.append("nutrition_estimate_outlier:sodium_mg>1500")
    if sugars is not None and sugars < 5.0 and label in DESSERT_LABEL_HINTS:
        warnings.append("nutrition_estimate_outlier:dessert_total_sugars_g<5")
    if calories is not None and calories > 550.0:
        warnings.append("nutrition_estimate_outlier:calories>550")
    return warnings


def _prediction_label_key(prediction: dict[str, Any]) -> str:
    value = prediction.get("label") or prediction.get("name") or prediction.get("dish") or ""
    return str(value).strip().lower().replace(" ", "_")


def _load_v2_dish_variant_evidence(
    connection: sqlite3.Connection,
    dish_key: str,
    limit: int = 12,
) -> list[dict[str, Any]]:
    if not _table_exists(connection, "dish_variant_evidence"):
        return []
    rows = connection.execute(
        """
        SELECT
            source,
            source_fdc_id,
            source_description,
            normalized_description,
            match_score,
            evidence_type,
            reason,
            created_at
        FROM dish_variant_evidence
        WHERE dish_key = ?
        ORDER BY
            CASE evidence_type
                WHEN 'matched_parent_variant' THEN 0
                WHEN 'filtered_composite_child' THEN 1
                ELSE 9
            END,
            COALESCE(match_score, 0.0) DESC,
            source_description
        LIMIT ?
        """,
        (dish_key, max(1, int(limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_v2_dish_ingredient_priors(
    connection: sqlite3.Connection,
    dish_predictions: list[dict[str, Any]],
    top_k_ingredients: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary: dict[str, Any] = {
        "available": False,
        "dish_label": None,
        "dish_id": None,
        "source": "dish_ingredient_priors",
        "row_count": 0,
    }
    if not (_table_exists(connection, "dish_labels") and _table_exists(connection, "dish_ingredient_priors")):
        return [], summary
    if not _table_has_rows(connection, "dish_ingredient_priors"):
        return [], summary
    dish_prior_columns = _table_columns(connection, "dish_ingredient_priors")
    has_ingredient_role = "ingredient_role" in dish_prior_columns

    top_prediction = dish_predictions[0] if dish_predictions else {}
    label = _prediction_label_key(top_prediction)
    dish = connection.execute(
        """
        SELECT d.dish_key, d.display_name, d.source
        FROM dish_labels d
        LEFT JOIN dish_aliases a ON a.dish_key = d.dish_key
        WHERE LOWER(d.dish_key) = LOWER(?)
           OR LOWER(d.display_name) = LOWER(?)
           OR LOWER(a.alias_name) = LOWER(?)
        ORDER BY CASE WHEN LOWER(d.dish_key) = LOWER(?) THEN 0 ELSE 1 END, d.dish_key
        LIMIT 1
        """,
        (label, label.replace("_", " "), label.replace("_", " "), label),
    ).fetchone()
    if dish is None:
        return [], summary

    evidence_rows = _load_v2_dish_variant_evidence(connection, str(dish["dish_key"]))

    rows = connection.execute(
        f"""
        SELECT
            p.canonical_ingredient_id,
            cf.canonical_name,
            cf.preferred_fdc_id,
            cf.preferred_source,
            p.mean_grams_per_100g_dish,
            p.median_grams_per_100g_dish,
            p.mode_grams_per_100g_dish,
            p.prevalence,
            p.recipe_count,
            p.source,
            {"p.ingredient_role" if has_ingredient_role else "NULL AS ingredient_role"},
            p.confidence_score
        FROM dish_ingredient_priors p
        INNER JOIN canonical_foods cf ON cf.canonical_food_id = p.canonical_ingredient_id
        WHERE p.dish_key = ?
        ORDER BY p.mean_grams_per_100g_dish DESC, cf.canonical_name
        LIMIT ?
        """,
        (str(dish["dish_key"]), max(1, int(top_k_ingredients))),
    ).fetchall()
    if not rows:
        return [], summary

    total_grams = sum(float(row["mean_grams_per_100g_dish"] or 0.0) for row in rows)
    ranked: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        grams_per_100g_dish = _as_float(row["mean_grams_per_100g_dish"]) or 0.0
        weight = (grams_per_100g_dish / total_grams) if total_grams > 0.0 else 0.0
        ranked.append(
            {
                "ingredient": row["canonical_name"],
                "normalized": row["canonical_name"],
                "matched_ingredient": row["canonical_name"],
                "fdc_id": row["preferred_fdc_id"],
                "canonical_source_type": row["preferred_source"],
                "weight": round(weight, 6),
                "weight_source": "db_dish_ingredient_priors",
                "mass_prior_source": "db_dish_ingredient_priors",
                "source_count": int(row["recipe_count"] or 0),
                "recipe_pct": float(row["prevalence"] or 0.0),
                "confidence": float(row["confidence_score"] or 0.0),
                "ingredient_role": row["ingredient_role"],
                "ingredient_rank": index,
                "grams_default": grams_per_100g_dish,
                "grams_mean": grams_per_100g_dish,
                "grams_mode": _as_float(row["mode_grams_per_100g_dish"]),
                "grams_median": _as_float(row["median_grams_per_100g_dish"]),
                "grams_std": None,
                "grams_per_100g_dish": grams_per_100g_dish,
                "original_grams_per_100g_dish": grams_per_100g_dish,
                "portion_method": "db_mean_100g",
                "n_weight_observations": int(row["recipe_count"] or 0),
                "default_include": 1,
                "included_in_nutrition": True,
                "contribution_basis": "ingredient_grams",
                "nutrition_prior_source": row["source"],
            }
        )

    summary.update(
        {
            "available": True,
            "dish_label": str(dish["dish_key"]),
            "display_name": str(dish["display_name"]),
            "dish_source": str(dish["source"]),
            "row_count": len(ranked),
            "total_grams_per_100g_dish": round(total_grams, 3),
            "frequency_100g_fallback_count": 0,
            "dish_variant_evidence_count": len(evidence_rows),
            "dish_variant_evidence": evidence_rows,
        }
    )
    return ranked, summary


def _load_db_dish_ingredient_priors(
    connection: sqlite3.Connection,
    dish_predictions: list[dict[str, Any]],
    top_k_ingredients: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    v2_ranked, v2_summary = _load_v2_dish_ingredient_priors(connection, dish_predictions, top_k_ingredients)
    if v2_ranked:
        return v2_ranked, v2_summary

    summary: dict[str, Any] = {
        "available": False,
        "dish_label": None,
        "dish_id": None,
        "source": "db_dish_ingredient_priors",
        "row_count": 0,
    }
    if not (_table_exists(connection, "dishes") and _table_exists(connection, "dish_ingredient_priors")):
        return [], summary
    if not _table_has_rows(connection, "dish_ingredient_priors"):
        return [], summary

    top_prediction = dish_predictions[0] if dish_predictions else {}
    label = _prediction_label_key(top_prediction)
    dish = connection.execute(
        """
        SELECT dish_id, food101_label, display_name
        FROM dishes
        WHERE LOWER(food101_label) = LOWER(?)
           OR LOWER(display_name) = LOWER(?)
        """,
        (label, label.replace("_", " ")),
    ).fetchone()
    if dish is None:
        return [], summary

    dish_id = int(dish[0])
    rows = connection.execute(
        """
        SELECT
            canonical_ingredient,
            ingredient_norm_key,
            fdc_id,
            usda_description,
            grams_default,
            grams_mean,
            grams_mode,
            grams_median,
            grams_std,
            portion_method,
            grams_per_100g_dish,
            pct_recipes_present,
            ingredient_rank,
            n_recipes,
            n_weight_observations,
            default_include,
            confidence,
            source
        FROM dish_ingredient_priors
        WHERE dish_id = ?
        ORDER BY COALESCE(ingredient_rank, 999999), canonical_ingredient
        LIMIT ?
        """,
        (dish_id, max(1, int(top_k_ingredients))),
    ).fetchall()
    if not rows:
        return [], summary

    total_grams = sum(float(row[10] or row[4] or 0.0) for row in rows if row[15])
    ranked: list[dict[str, Any]] = []
    for row in rows:
        grams_default = _as_float(row[4])
        grams_per_100g_dish = _as_float(row[10]) or grams_default
        default_include = bool(row[15])
        weight = (grams_per_100g_dish / total_grams) if default_include and grams_per_100g_dish and total_grams > 0.0 else 0.0
        ranked.append(
            {
                "ingredient": row[0],
                "normalized": row[1] or row[0],
                "matched_ingredient": row[3],
                "fdc_id": row[2],
                "weight": round(weight, 6),
                "weight_source": "db_dish_ingredient_priors",
                "mass_prior_source": "db_dish_ingredient_priors",
                "source_count": row[13],
                "recipe_pct": row[11],
                "confidence": row[16],
                "ingredient_rank": row[12],
                "grams_default": grams_default,
                "grams_mean": _as_float(row[5]),
                "grams_mode": _as_float(row[6]),
                "grams_median": _as_float(row[7]),
                "grams_std": _as_float(row[8]),
                "grams_per_100g_dish": grams_per_100g_dish,
                "portion_method": row[9],
                "n_weight_observations": row[14],
                "default_include": int(default_include),
                "included_in_nutrition": default_include,
                "contribution_basis": "ingredient_grams",
                "nutrition_prior_source": row[17],
            }
        )

    summary.update(
        {
            "available": True,
            "dish_label": str(dish[1]),
            "dish_id": dish_id,
            "display_name": dish[2],
            "row_count": len(ranked),
            "total_grams_per_100g_dish": round(total_grams, 3),
            "frequency_100g_fallback_count": sum(
                1 for item in ranked if item.get("portion_method") == "frequency_100g_fallback"
            ),
        }
    )
    return ranked, summary


def _bridge_from_ranked_ingredients(
    *,
    normalized_predictions: list[dict[str, Any]],
    ingredients_ranked: list[dict[str, Any]],
    database_path: Path,
    warnings: list[str],
    nutrition_label: str,
    requested_weight_source: str,
    actual_weight_source: str,
    source_precedence: dict[str, Any],
    extra_palm_ready: dict[str, Any] | None = None,
) -> BridgeResult:
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    with closing(sqlite3.connect(database_path)) as connection:
        support_warnings = _lookup_support_warnings(connection)
        if "no_supported_ingredient_nutrition_table" in support_warnings:
            return _pending_result(
                normalized_predictions,
                ", ".join(support_warnings),
                ingredients_ranked,
            )
        warnings.extend(warning for warning in support_warnings if warning not in warnings)
        for ingredient_record in ingredients_ranked:
            ingredient = str(ingredient_record["ingredient"])
            nutrition, match_debug = _lookup_nutrition(connection, ingredient, database_path)
            if nutrition is None:
                unmatched.append(
                    {
                        **ingredient_record,
                        "ingredient": ingredient,
                        "nutrition_source": "not_found",
                        "included_in_nutrition": False,
                        **match_debug,
                    }
                )
                continue
            nutrition_payload = asdict(nutrition)
            matched_record = {
                **ingredient_record,
                "ingredient": ingredient,
                "normalized": match_debug["normalized"],
                "matched_ingredient": match_debug["matched_ingredient"],
                "matched_table": match_debug.get("matched_table") or match_debug.get("db_table"),
                "matched_column": match_debug.get("matched_column"),
                "match_method": match_debug["match_method"],
                "match_score": match_debug.get("match_score"),
                "status": match_debug["status"],
                "aliases_tried": match_debug["aliases_tried"],
                "calories_unit_adjustment": match_debug.get("calories_unit_adjustment"),
                "total_fat_fallback_applied": bool(match_debug.get("total_fat_fallback_applied")),
                "fat_components_g": match_debug.get("fat_components_g"),
                "sugar_fallback_applied": bool(match_debug.get("sugar_fallback_applied")),
                "nutrition_source": "usda_generic",
                "nutrition_per_100g": {key: nutrition_payload.get(key) for key in NUTRIENT_KEYS},
                "source": nutrition.source,
                "fdc_id": nutrition.fdc_id,
            }
            matched.append(matched_record)

    matched = _with_nutrition_contributions(matched)
    estimated = _aggregate_nutrition(matched) if matched else None
    outlier_flags = _outlier_warnings(
        str(normalized_predictions[0].get("label") or normalized_predictions[0].get("name") or "") if normalized_predictions else None,
        estimated,
    )
    warnings.extend(outlier_flags)
    warnings.extend(_contribution_sum_warnings(estimated, matched))
    if ingredients_ranked and len(unmatched) / max(1, len(ingredients_ranked)) > 0.5:
        warnings.append("ingredient_lookup_low_coverage")
    if not matched:
        warnings.append("no_included_found_ingredients_for_nutrition")
        warnings.append("No blended ingredients matched generic_ingredient_nutrition")
    collapse_groups = Counter(
        item.get("nutrition_collapse_key")
        for item in matched
        if item.get("included_in_nutrition", True) and item.get("nutrition_collapse_key")
    )
    nutrition_collapse_summary = {
        "basis": "fdc_id_or_usda_match",
        "input_ingredient_rows": len([item for item in matched if item.get("included_in_nutrition", True)]),
        "nutrition_source_rows": len(collapse_groups),
        "collapsed_group_count": len([count for count in collapse_groups.values() if count > 1]),
    }

    palm_ready = {
        "status": "ready" if matched else "pending",
        "label": nutrition_label,
        "estimated_nutrition_per_100g": estimated,
        "matched_ingredients": matched,
        "nutrition_composition": [_contribution_row(item) for item in matched],
        "unmatched_ingredients": unmatched,
        "outlier_flags": outlier_flags,
        "note": "This estimate is calculated from DB-backed ingredient portion priors.",
        "requested_weight_source": requested_weight_source,
        "actual_weight_source": actual_weight_source,
        "nutrition_collapse_summary": nutrition_collapse_summary,
        "source_precedence": source_precedence,
    }
    if extra_palm_ready:
        palm_ready.update(extra_palm_ready)

    ingredient_normalization = {
        "matched": [
            {
                "raw_ingredient": item["ingredient"],
                "normalized": item.get("normalized"),
                "canonical": item.get("matched_ingredient") or item["ingredient"],
                "matched_ingredient": item.get("matched_ingredient"),
                "usda_match_label": item.get("source"),
                "usda_match_method": item.get("match_method") or "generic_ingredient_nutrition",
                "match_method": item.get("match_method"),
                "matched_table": item.get("matched_table"),
                "matched_column": item.get("matched_column"),
                "status": item.get("status"),
                "weight_source": item.get("weight_source"),
                "nutrition_source": item.get("nutrition_source"),
                "confidence": item.get("confidence"),
                "fdc_id": item.get("fdc_id"),
                "included_in_nutrition": item.get("included_in_nutrition", True),
                "normalized_weight_used": item.get("normalized_weight_used"),
                "nutrition_per_100g": item.get("nutrition_per_100g"),
                "nutrient_contributions": item.get("nutrient_contributions"),
                "grams_default": item.get("grams_default"),
                "grams_per_100g_dish": item.get("grams_per_100g_dish"),
                "portion_method": item.get("portion_method"),
                "nutrition_collapse_key": item.get("nutrition_collapse_key"),
                "nutrition_group_size": item.get("nutrition_group_size"),
                "nutrition_group_representative": item.get("nutrition_group_representative"),
                "nutrition_collapsed_into": item.get("nutrition_collapsed_into"),
            }
            for item in matched
        ],
        "unmatched": unmatched,
        "summary": {"matched_count": len(matched), "unmatched_count": len(unmatched)},
    }
    warnings = list(dict.fromkeys(warnings))
    return BridgeResult(
        status=str(palm_ready["status"]),
        dish_predictions=normalized_predictions,
        ingredients_ranked=ingredients_ranked,
        visible_ingredients=ingredients_ranked,
        ingredient_normalization=ingredient_normalization,
        palm_ready=palm_ready,
        warnings=warnings,
    )


def build_food_bridge(
    dish_predictions: list[dict[str, Any]],
    db_path: str | Path | None = None,
    dish_priors_path: str | Path = DISH_PRIORS_PATH,
    dish_mass_priors_path: str | Path = DISH_MASS_PRIORS_PATH,
    nutrition5k_priors_path: str | Path = NUTRITION5K_MASS_PRIORS_PATH,
    top_k_dishes: int = 5,
    top_k_ingredients: int = 20,
    use_dish_mass_priors: bool = False,
    use_nutrition5k: bool = False,
) -> BridgeResult:
    normalized_predictions = [dict(prediction) for prediction in dish_predictions]
    database_path = resolve_runtime_db_path(db_path)
    db_prior_summary: dict[str, Any] = {}
    use_db_dish_priors = Path(dish_priors_path) == Path(DISH_PRIORS_PATH)
    if use_db_dish_priors and database_path.exists():
        with closing(sqlite3.connect(database_path)) as connection:
            connection.row_factory = sqlite3.Row
            db_ranked, db_prior_summary = _load_db_dish_ingredient_priors(
                connection,
                normalized_predictions,
                top_k_ingredients,
            )
        if db_ranked:
            db_warnings: list[str] = []
            if int(db_prior_summary.get("frequency_100g_fallback_count") or 0):
                db_warnings.append("db_dish_prior_frequency_100g_fallback")
            return _bridge_from_ranked_ingredients(
                normalized_predictions=normalized_predictions,
                ingredients_ranked=db_ranked,
                database_path=database_path,
                warnings=db_warnings,
                nutrition_label="Estimated nutrition from DB-backed dish ingredient portion priors",
                requested_weight_source="db_dish_ingredient_priors",
                actual_weight_source="db_dish_ingredient_priors",
                source_precedence={
                    "dish_prediction_source": "vision_classifier",
                    "dish_source": "dishes",
                    "ingredient_presence_source": "dish_ingredient_priors",
                    "ingredient_portion_source": "dish_ingredient_priors.grams_per_100g_dish",
                    "ingredient_nutrient_source": "usda_generic",
                },
                extra_palm_ready={
                    "nutrition_basis": "dish_ingredient_priors_grams",
                    "db_dish_prior_summary": db_prior_summary,
                    "dish_mass_priors_requested": bool(use_dish_mass_priors),
                    "dish_mass_prior_applied_ingredient_count": len(db_ranked),
                    "dish_mass_prior_missing_labels": [],
                    "dish_mass_prior_low_coverage_labels": [],
                    "nutrition5k_requested": bool(use_nutrition5k),
                    "nutrition5k_matched_dish_count": 0,
                    "nutrition5k_applied_ingredient_count": 0,
                    "nutrition5k_top_matches": [],
                },
            )

    try:
        priors = _load_priors(Path(dish_priors_path))
    except (FileNotFoundError, ValueError) as exc:
        return _pending_result(normalized_predictions, str(exc))

    ingredients_ranked, missing_priors = _blend_ingredients(normalized_predictions, priors, top_k_dishes, top_k_ingredients)
    if not ingredients_ranked:
        warning = "No dish ingredient priors matched the classifier predictions"
        if missing_priors:
            warning += f": {', '.join(sorted(set(missing_priors)))}"
        return _pending_result(normalized_predictions, warning)

    warnings: list[str] = []
    ingredients_ranked, capped_count = _apply_recipe_weight_caps(ingredients_ranked)
    if capped_count:
        warnings.append(f"Applied seasoning caps to {capped_count} RecipeNLG frequency-weighted ingredients.")

    dish_mass_summary: dict[str, Any] = {
        "requested": bool(use_dish_mass_priors),
        "applied_ingredient_count": 0,
        "missing_labels": [],
        "low_coverage_labels": [],
    }
    if use_dish_mass_priors:
        try:
            dish_mass_priors = _load_dish_mass_priors(Path(dish_mass_priors_path))
        except (FileNotFoundError, ValueError) as exc:
            warnings.append(str(exc))
        else:
            mass_ranked, dish_mass_summary = _blend_dish_mass_priors(
                normalized_predictions,
                dish_mass_priors,
                top_k_dishes,
                top_k_ingredients,
                {normalize_ingredient(str(item.get("ingredient") or "")) or "" for item in ingredients_ranked},
            )
            if mass_ranked:
                missing_required = _missing_required_top_dish_ingredients(normalized_predictions, ingredients_ranked, mass_ranked)
                if missing_required:
                    warnings.append(f"dish_mass_prior_missing_required_ingredients:{','.join(missing_required)}")
                    warnings.append("dish_mass_prior_low_coverage")
                    dish_mass_summary["low_coverage_labels"] = list(
                        dict.fromkeys((dish_mass_summary.get("low_coverage_labels") or []) + missing_required)
                    )
                    dish_mass_summary["applied_ingredient_count"] = 0
                else:
                    ingredients_ranked = mass_ranked
            else:
                warnings.append("dish_mass_prior_low_coverage")
            if dish_mass_summary.get("low_coverage_labels"):
                warnings.append("dish_mass_prior_low_coverage")

    nutrition5k_summary: dict[str, Any] = {
        "requested": bool(use_nutrition5k),
        "matched_dish_count": 0,
        "applied_ingredient_count": 0,
        "top_matches": [],
    }
    if use_nutrition5k and not any(str(item.get("weight_source")) == "recipenlg_measurements_usda_portions" for item in ingredients_ranked):
        try:
            nutrition5k_payload = _load_nutrition5k_mass_priors(Path(nutrition5k_priors_path))
        except (FileNotFoundError, ValueError) as exc:
            warnings.append(str(exc))
        else:
            ingredients_ranked, nutrition5k_summary = _apply_nutrition5k_mass_priors(ingredients_ranked, nutrition5k_payload)
            applied_count = int(nutrition5k_summary.get("applied_ingredient_count") or 0)
            matched_dish_count = int(nutrition5k_summary.get("matched_dish_count") or 0)
            if applied_count:
                warnings.append(
                    "Nutrition5k mass priors enabled for overlapping ingredients. "
                    "USDA/generic nutrition remains the nutrient source."
                )
            elif matched_dish_count < NUTRITION5K_MIN_MATCHED_DISHES:
                warnings.append("nutrition5k_no_confident_overlap")
                warnings.append("nutrition5k_mass_prior_unavailable")
            else:
                warnings.append("nutrition5k_mass_prior_unavailable")

    ingredients_ranked = sorted(
        ingredients_ranked,
        key=lambda item: (float(item.get("weight") or 0.0), int(item.get("source_count") or 0), str(item.get("ingredient") or "")),
        reverse=True,
    )[: max(1, top_k_ingredients)]

    if not database_path.exists():
        return _pending_result(normalized_predictions, f"PALM database not found: {database_path}", ingredients_ranked)

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    if missing_priors:
        warnings.append(f"Missing dish priors for: {', '.join(sorted(set(missing_priors)))}")

    with closing(sqlite3.connect(database_path)) as connection:
        support_warnings = _lookup_support_warnings(connection)
        if "no_supported_ingredient_nutrition_table" in support_warnings:
            return _pending_result(
                normalized_predictions,
                ", ".join(support_warnings),
                ingredients_ranked,
            )
        warnings.extend(warning for warning in support_warnings if warning not in warnings)
        for ingredient_record in ingredients_ranked:
            ingredient = str(ingredient_record["ingredient"])
            nutrition, match_debug = _lookup_nutrition(connection, ingredient, database_path)
            if nutrition is None:
                unmatched.append(
                    {
                        "ingredient": ingredient,
                        "weight": ingredient_record["weight"],
                        "weight_source": ingredient_record.get("weight_source", "recipenlg_frequency"),
                        "mass_prior_source": ingredient_record.get("mass_prior_source", "recipenlg_frequency"),
                        "original_frequency_weight": ingredient_record.get("original_frequency_weight", ingredient_record["weight"]),
                        "mass_weight": ingredient_record.get("mass_weight"),
                        "median_grams": ingredient_record.get("median_grams"),
                        "nutrition5k_match_count": ingredient_record.get("nutrition5k_match_count"),
                        "nutrition_source": "not_found",
                        **match_debug,
                    }
                )
                continue
            nutrition_payload = asdict(nutrition)
            matched_record = {
                "ingredient": ingredient,
                "normalized": match_debug["normalized"],
                "matched_ingredient": match_debug["matched_ingredient"],
                "matched_table": match_debug.get("matched_table") or match_debug.get("db_table"),
                "matched_column": match_debug.get("matched_column"),
                "match_method": match_debug["match_method"],
                "match_score": match_debug.get("match_score"),
                "status": match_debug["status"],
                "aliases_tried": match_debug["aliases_tried"],
                "calories_unit_adjustment": match_debug.get("calories_unit_adjustment"),
                "total_fat_fallback_applied": bool(match_debug.get("total_fat_fallback_applied")),
                "fat_components_g": match_debug.get("fat_components_g"),
                "sugar_fallback_applied": bool(match_debug.get("sugar_fallback_applied")),
                "weight": ingredient_record["weight"],
                "weight_source": ingredient_record.get("weight_source", "recipenlg_frequency"),
                "mass_prior_source": ingredient_record.get("mass_prior_source", "recipenlg_frequency"),
                "nutrition_source": "usda_generic",
                "weight_adjustment": ingredient_record.get("weight_adjustment"),
                "weight_before_cap": ingredient_record.get("weight_before_cap"),
                "original_frequency_weight": ingredient_record.get("original_frequency_weight", ingredient_record["weight"]),
                "mass_weight": ingredient_record.get("mass_weight"),
                "median_grams": ingredient_record.get("median_grams"),
                "nutrition5k_match_count": ingredient_record.get("nutrition5k_match_count"),
                "recipenlg_frequency_weight": ingredient_record.get("recipenlg_frequency_weight", ingredient_record["weight"]),
                "confidence": ingredient_record["confidence"],
                "source_count": ingredient_record["source_count"],
                "nutrition_per_100g": {key: nutrition_payload.get(key) for key in NUTRIENT_KEYS},
                "source": nutrition.source,
                "fdc_id": nutrition.fdc_id,
            }
            matched_record["nutrition_contribution_per_100g"] = _contribution_row(matched_record)
            matched.append(matched_record)

    matched = _with_nutrition_contributions(matched)
    estimated = _aggregate_nutrition(matched) if matched else None
    outlier_flags = _outlier_warnings(
        str(normalized_predictions[0].get("label") or "") if normalized_predictions else None,
        estimated,
    )
    warnings.extend(outlier_flags)
    warnings.extend(_contribution_sum_warnings(estimated, matched))
    if ingredients_ranked and len(unmatched) / max(1, len(ingredients_ranked)) > 0.5:
        warnings.append("ingredient_lookup_low_coverage")
    actual_weight_source = (
        "recipenlg_measurements_usda_portions"
        if any(str(item.get("weight_source")) == "recipenlg_measurements_usda_portions" for item in matched + unmatched + ingredients_ranked)
        else (
            "nutrition5k_mass_prior"
            if any(str(item.get("weight_source")) == "nutrition5k_mass_prior" for item in matched + unmatched + ingredients_ranked)
            else "recipenlg_frequency"
        )
    )
    if actual_weight_source == "recipenlg_measurements_usda_portions":
        nutrition_label = "Estimated nutrition per 100g from RecipeNLG measurement-derived USDA portion mass priors"
    else:
        nutrition_label = (
        "Estimated nutrition per 100g from Nutrition5k-informed mass-weighted ingredient priors"
        if actual_weight_source == "nutrition5k_mass_prior"
        else "Estimated nutrition per 100g from frequency-weighted ingredient priors"
        )
    palm_ready = {
        "status": "ready" if matched else "pending",
        "label": nutrition_label,
        "estimated_nutrition_per_100g": estimated,
        "matched_ingredients": matched,
        "nutrition_composition": [_contribution_row(item) for item in matched],
        "unmatched_ingredients": unmatched,
        "outlier_flags": outlier_flags,
        "note": "This is an ingredient-prior estimate, not a portion-estimated meal result.",
        "requested_weight_source": (
            "recipenlg_measurements_usda_portions"
            if use_dish_mass_priors
            else ("nutrition5k_mass_prior" if use_nutrition5k else "recipenlg_frequency")
        ),
        "actual_weight_source": actual_weight_source,
        "dish_mass_priors_requested": bool(use_dish_mass_priors),
        "dish_mass_prior_applied_ingredient_count": int(dish_mass_summary.get("applied_ingredient_count") or 0),
        "dish_mass_prior_missing_labels": dish_mass_summary.get("missing_labels") or [],
        "dish_mass_prior_low_coverage_labels": dish_mass_summary.get("low_coverage_labels") or [],
        "nutrition5k_requested": bool(use_nutrition5k),
        "nutrition5k_matched_dish_count": int(nutrition5k_summary.get("matched_dish_count") or 0),
        "nutrition5k_applied_ingredient_count": int(nutrition5k_summary.get("applied_ingredient_count") or 0),
        "nutrition5k_top_matches": nutrition5k_summary.get("top_matches") or [],
        "source_precedence": {
            "dish_prediction_source": "vision_classifier",
            "ingredient_presence_source": "recipenlg_priors",
            "ingredient_nutrient_source": "usda_generic",
            "ingredient_mass_weight_source_requested": (
                "recipenlg_measurements_usda_portions"
                if use_dish_mass_priors
                else ("nutrition5k_mass_prior" if use_nutrition5k else "recipenlg_frequency")
            ),
            "ingredient_mass_weight_source_actual": actual_weight_source,
        },
    }
    if not matched:
        warnings.append("no_included_found_ingredients_for_nutrition")
    ingredient_normalization = {
        "matched": [
            {
                "raw_ingredient": item["ingredient"],
                "normalized": item.get("normalized"),
                "canonical": item.get("matched_ingredient") or item["ingredient"],
                "matched_ingredient": item.get("matched_ingredient"),
                "usda_match_label": item.get("source"),
                "usda_match_method": item.get("match_method") or "generic_ingredient_nutrition",
                "match_method": item.get("match_method"),
                "matched_table": item.get("matched_table"),
                "matched_column": item.get("matched_column"),
                "status": item.get("status"),
                "weight_source": item.get("weight_source"),
                "mass_prior_source": item.get("mass_prior_source"),
                "original_frequency_weight": item.get("original_frequency_weight"),
                "mass_weight": item.get("mass_weight"),
                "median_grams": item.get("median_grams"),
                "nutrition5k_match_count": item.get("nutrition5k_match_count"),
                "nutrition_source": item.get("nutrition_source"),
                "embedding_backend": "none",
                "confidence": item.get("confidence"),
                "fdc_id": item.get("fdc_id"),
                "included_in_nutrition": item.get("included_in_nutrition", True),
                "normalized_weight_used": item.get("normalized_weight_used"),
                "nutrition_per_100g": item.get("nutrition_per_100g"),
                "nutrient_contributions": item.get("nutrient_contributions"),
            }
            for item in matched
        ],
        "unmatched": unmatched,
        "summary": {"matched_count": len(matched), "unmatched_count": len(unmatched)},
    }
    if not matched:
        warnings.append("No blended ingredients matched generic_ingredient_nutrition")
    warnings = list(dict.fromkeys(warnings))
    return BridgeResult(
        status=str(palm_ready["status"]),
        dish_predictions=normalized_predictions,
        ingredients_ranked=ingredients_ranked,
        visible_ingredients=ingredients_ranked,
        ingredient_normalization=ingredient_normalization,
        palm_ready=palm_ready,
        warnings=warnings,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PALM food bridge for one dish prediction.")
    parser.add_argument("--dish")
    parser.add_argument("--confidence", type=float, default=0.85)
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--dish-priors-path", type=Path, default=DISH_PRIORS_PATH)
    parser.add_argument("--dish-mass-priors-path", type=Path, default=DISH_MASS_PRIORS_PATH)
    parser.add_argument("--nutrition5k-priors-path", type=Path, default=NUTRITION5K_MASS_PRIORS_PATH)
    parser.add_argument("--top-k-ingredients", type=int, default=15)
    parser.add_argument("--use-dish-mass-priors", action="store_true", help="Apply RecipeNLG measurement mass priors built from USDA portion tables.")
    parser.add_argument("--use-nutrition5k", action="store_true", help="Apply optional Nutrition5k dish-overlap mass priors from the offline artifact.")
    parser.add_argument("--debug-ingredients", nargs="*", help="Print local nutrition lookup diagnostics for specific ingredients.")
    parser.add_argument("--audit-db", action="store_true", help="Print nutrition lookup DB/table/column diagnostics.")
    parser.add_argument("--audit-ingredients", action="store_true", help="Audit common ingredient lookup coverage.")
    parser.add_argument("--audit-common-ingredients", action="store_true", help="Audit common PALM pasta, cheese, and staple ingredient variants.")
    parser.add_argument("--seed-ingredient-aliases", action="store_true", help="Create/update the ingredient_aliases canonical backbone table.")
    parser.add_argument("--seed-common-ingredient-rows", action="store_true", help="Insert missing exact common ingredient rows by copying existing USDA-backed generic nutrition rows.")
    parser.add_argument("--cleanup-noncanonical-ingredient-rows", action="store_true", help="Remove alias-copy rows from generic_ingredient_nutrition when they are represented in ingredient_aliases.")
    args = parser.parse_args()

    if args.audit_db:
        report = audit_nutrition_db(args.db_path)
        print("PALM Food Bridge DB Audit")
        print("=" * 25)
        print(json.dumps(report, indent=2))
        return

    if args.seed_common_ingredient_rows:
        alias_result = seed_ingredient_alias_backbone(args.db_path)
        result = seed_common_ingredient_nutrition_alias_rows(args.db_path)
        result["ingredient_aliases"] = alias_result
        print("PALM Food Bridge Common Ingredient Row Seed")
        print("=" * 45)
        print(json.dumps(result, indent=2))
        return

    if args.cleanup_noncanonical_ingredient_rows:
        result = cleanup_noncanonical_ingredient_nutrition_alias_rows(args.db_path)
        print("PALM Food Bridge Noncanonical Ingredient Row Cleanup")
        print("=" * 55)
        print(json.dumps(result, indent=2))
        return

    if args.seed_ingredient_aliases:
        result = seed_ingredient_alias_backbone(args.db_path)
        print("PALM Food Bridge Ingredient Alias Backbone Seed")
        print("=" * 52)
        print(json.dumps(result, indent=2))
        return

    if args.audit_ingredients:
        print("PALM Food Bridge Ingredient Lookup Audit")
        print("=" * 40)
        print("ingredient | normalized | status | matched_table | matched_ingredient | match_method | reason")
        for ingredient in AUDIT_INGREDIENTS:
            result = lookup_ingredient_nutrition(ingredient, args.db_path)
            print(
                " | ".join(
                    [
                        ingredient,
                        str(result.get("normalized_ingredient")),
                        str(result.get("status")),
                        str(result.get("matched_table")),
                        str(result.get("matched_ingredient")),
                        str(result.get("match_method")),
                        str(result.get("reason")),
                    ]
                )
            )
        return

    if args.audit_common_ingredients:
        print("PALM Food Bridge Common Ingredient Audit")
        print("=" * 42)
        print("query | normalized | aliases_tried | status | matched_ingredient | matched_table | match_method | fdc_id | description")
        for ingredient in COMMON_AUDIT_INGREDIENTS:
            result = lookup_ingredient_nutrition(ingredient, args.db_path)
            print(
                " | ".join(
                    [
                        ingredient,
                        str(result.get("normalized_ingredient")),
                        ",".join(str(item) for item in result.get("alias_candidates_tried") or []),
                        str(result.get("status")),
                        str(result.get("matched_ingredient")),
                        str(result.get("matched_table")),
                        str(result.get("match_method")),
                        str(result.get("fdc_id")),
                        str(result.get("matched_ingredient") or result.get("nearest_candidates") or ""),
                    ]
                )
            )
        return

    if args.debug_ingredients:
        print("PALM Food Bridge Ingredient Diagnostics")
        print("=" * 39)
        for ingredient in args.debug_ingredients:
            result = lookup_ingredient_nutrition(ingredient, args.db_path)
            print(json.dumps(result, indent=2))
        return

    if not args.dish:
        parser.error("--dish is required unless --debug-ingredients is provided")

    result = build_food_bridge(
        [{"label": args.dish, "name": args.dish.replace("_", " "), "confidence": args.confidence}],
        db_path=args.db_path,
        dish_priors_path=args.dish_priors_path,
        dish_mass_priors_path=args.dish_mass_priors_path,
        nutrition5k_priors_path=args.nutrition5k_priors_path,
        top_k_ingredients=args.top_k_ingredients,
        use_dish_mass_priors=args.use_dish_mass_priors,
        use_nutrition5k=args.use_nutrition5k,
    )
    print("PALM Food Bridge")
    print("=" * 16)
    print(f"Status: {result.status}")
    print(f"Warnings: {', '.join(result.warnings) if result.warnings else 'none'}")
    print(f"Requested weight source: {result.palm_ready.get('requested_weight_source')}")
    print(f"Actual weight source: {result.palm_ready.get('actual_weight_source')}")
    print(f"Nutrition5k matched dish count: {result.palm_ready.get('nutrition5k_matched_dish_count')}")
    print("Blended ingredients:")
    for ingredient in result.ingredients_ranked[: args.top_k_ingredients]:
        print(f"  - {ingredient['ingredient']}: {ingredient['weight']:.3f} ({ingredient.get('weight_source', 'recipenlg_frequency')})")
    normalization = result.ingredient_normalization.get("summary") or {}
    print(f"Matched ingredients: {normalization.get('matched_count', 0)}")
    print(f"Unmatched ingredients: {normalization.get('unmatched_count', 0)}")
    print("Estimated nutrition per 100g:")
    print(json.dumps(result.palm_ready.get("estimated_nutrition_per_100g"), indent=2))
    print("Ingredient contribution table (per 100g estimate):")
    for item in result.palm_ready.get("nutrition_composition") or []:
        print(
            json.dumps(
                {
                    "ingredient": item.get("ingredient"),
                    "normalized": item.get("normalized"),
                    "matched_ingredient": item.get("matched_ingredient"),
                    "match_method": item.get("match_method"),
                    "weight": item.get("weight"),
                    "weight_source": item.get("weight_source"),
                    "nutrition_source": item.get("nutrition_source"),
                    "calories_contribution": item.get("calories_contribution"),
                    "protein_contribution": item.get("protein_contribution"),
                    "fat_contribution": item.get("fat_contribution"),
                    "carbs_contribution": item.get("carbs_contribution"),
                    "sugar_contribution": item.get("sugar_contribution"),
                    "sodium_contribution": item.get("sodium_contribution"),
                }
            )
        )


if __name__ == "__main__":
    main()
