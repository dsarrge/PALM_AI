from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from food_ai.food_bridge.config import (
    NUTRITION5K_DISH_INGREDIENTS_PATH,
    NUTRITION5K_DISH_NUTRITION_PATH,
    NUTRITION5K_INGREDIENTS_METADATA_PATH,
    NUTRITION5K_MASS_PRIORS_PATH,
)
from food_ai.food_bridge.ingredient_normalize import normalize_ingredient
from food_ai.food_bridge.nutrition5k_loader import detect_nutrition5k_schema


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _round_or_none(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def _load_metadata(path: Path, detected: dict[str, Any]) -> dict[str, str]:
    ingredient_id_col = detected.get("ingredient_id")
    ingredient_name_col = detected.get("ingredient_name")
    if not path.exists() or not ingredient_id_col or not ingredient_name_col:
        return {}

    metadata: dict[str, str] = {}
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ingredient_id = str(row.get(ingredient_id_col) or "").strip()
            ingredient_name = str(row.get(ingredient_name_col) or "").strip()
            if ingredient_id and ingredient_name:
                metadata[ingredient_id] = ingredient_name
    return metadata


def _load_dish_nutrition(path: Path, detected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    dish_id_col = detected.get("dish_id")
    mass_col = detected.get("gram_or_mass")
    nutrition_cols = detected.get("nutrition") or {}
    if not path.exists() or not dish_id_col:
        return {}

    values: dict[str, dict[str, Any]] = {}
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            dish_id = str(row.get(dish_id_col) or "").strip()
            if not dish_id:
                continue
            total_mass = _as_float(row.get(mass_col)) if mass_col else None
            calories = _as_float(row.get(nutrition_cols.get("calories")))
            protein = _as_float(row.get(nutrition_cols.get("protein")))
            fat = _as_float(row.get(nutrition_cols.get("fat")))
            carbs = _as_float(row.get(nutrition_cols.get("carb")))
            per_100g = None
            if total_mass and total_mass > 0:
                per_100g = {
                    "calories": _round_or_none(calories / total_mass * 100.0),
                    "protein_g": _round_or_none(protein / total_mass * 100.0),
                    "total_fat_g": _round_or_none(fat / total_mass * 100.0),
                    "total_carbs_g": _round_or_none(carbs / total_mass * 100.0),
                    "fiber_g": None,
                    "total_sugars_g": None,
                    "sodium_mg": None,
                }
            values[dish_id] = {
                "total_mass_g": total_mass,
                "nutrition_per_100g": per_100g,
            }
    return values


def build_nutrition5k_mass_priors(
    dish_ingredients_path: Path = NUTRITION5K_DISH_INGREDIENTS_PATH,
    dish_nutrition_path: Path = NUTRITION5K_DISH_NUTRITION_PATH,
    ingredients_metadata_path: Path = NUTRITION5K_INGREDIENTS_METADATA_PATH,
    output_path: Path = NUTRITION5K_MASS_PRIORS_PATH,
) -> dict[str, Any]:
    schema = detect_nutrition5k_schema(
        dish_ingredients_path=dish_ingredients_path,
        dish_nutrition_path=dish_nutrition_path,
        ingredients_metadata_path=ingredients_metadata_path,
    )
    if not schema["ok"]:
        raise ValueError("Nutrition5k missing required columns: " + "; ".join(schema["errors"]))

    detected = schema["detected"]
    ingredient_cols = detected["dish_ingredients"]
    metadata_by_id = _load_metadata(ingredients_metadata_path, detected["ingredients_metadata"])
    nutrition_by_dish = _load_dish_nutrition(dish_nutrition_path, detected["dish_nutrition_values"])

    dish_id_col = ingredient_cols["dish_id"]
    ingredient_id_col = ingredient_cols.get("ingredient_id")
    ingredient_name_col = ingredient_cols["ingredient_name"]
    grams_col = ingredient_cols["gram_or_mass"]

    dish_ingredients: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    raw_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    scanned_rows = 0
    used_rows = 0

    with dish_ingredients_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            scanned_rows += 1
            dish_id = str(row.get(dish_id_col) or "").strip()
            ingredient_id = str(row.get(ingredient_id_col) or "").strip() if ingredient_id_col else ""
            ingredient_name = str(row.get(ingredient_name_col) or "").strip()
            ingredient_name = ingredient_name or metadata_by_id.get(ingredient_id, "")
            grams = _as_float(row.get(grams_col))
            normalized = normalize_ingredient(ingredient_name)
            if not dish_id or not normalized or grams is None or grams <= 0:
                continue
            dish_ingredients[dish_id][normalized] += grams
            raw_names[(dish_id, normalized)].add(ingredient_name)
            used_rows += 1

    nutrition5k_dishes: dict[str, Any] = {}
    for dish_id, ingredient_masses in sorted(dish_ingredients.items()):
        nutrition_record = nutrition_by_dish.get(dish_id) or {}
        total_mass = _as_float(nutrition_record.get("total_mass_g"))
        ingredient_mass_total = sum(ingredient_masses.values())
        if total_mass is None or total_mass <= 0:
            total_mass = ingredient_mass_total
        if total_mass <= 0:
            continue

        ingredients = []
        for ingredient, mass_g in sorted(ingredient_masses.items()):
            ingredients.append(
                {
                    "ingredient": ingredient,
                    "raw_ingredients": sorted(raw_names.get((dish_id, ingredient), [])),
                    "mass_g": round(mass_g, 6),
                    "mass_fraction": round(mass_g / total_mass, 6),
                }
            )

        nutrition5k_dishes[dish_id] = {
            "total_mass_g": round(total_mass, 6),
            "nutrition_per_100g": nutrition_record.get("nutrition_per_100g"),
            "ingredients": sorted(ingredients, key=lambda item: item["mass_fraction"], reverse=True),
        }

    unique_ingredients = {
        ingredient["ingredient"]
        for dish in nutrition5k_dishes.values()
        for ingredient in dish["ingredients"]
    }

    payload = {
        "metadata": {
            "source": "Nutrition5k simplified CSVs",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dish_count": len(nutrition5k_dishes),
            "ingredient_count": len(unique_ingredients),
            "rows_scanned": scanned_rows,
            "rows_used": used_rows,
            "warnings": schema["warnings"],
            "note": "Nutrition5k dish IDs are not Food-101 labels. This artifact is used for ingredient-overlap mass priors.",
            "input_files": {
                "dish_ingredients": str(dish_ingredients_path),
                "dish_nutrition_values": str(dish_nutrition_path),
                "ingredients_metadata": str(ingredients_metadata_path),
            },
        },
        "nutrition5k_dishes": nutrition5k_dishes,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Nutrition5k dish-overlap mass priors for PALM food bridge.")
    parser.add_argument("--dish-ingredients", type=Path, default=NUTRITION5K_DISH_INGREDIENTS_PATH)
    parser.add_argument("--dish-nutrition", type=Path, default=NUTRITION5K_DISH_NUTRITION_PATH)
    parser.add_argument("--ingredients-metadata", type=Path, default=NUTRITION5K_INGREDIENTS_METADATA_PATH)
    parser.add_argument("--output", type=Path, default=NUTRITION5K_MASS_PRIORS_PATH)
    args = parser.parse_args()

    print("PALM Nutrition5k Mass Prior Build")
    print("=" * 34)
    payload = build_nutrition5k_mass_priors(
        dish_ingredients_path=args.dish_ingredients,
        dish_nutrition_path=args.dish_nutrition,
        ingredients_metadata_path=args.ingredients_metadata,
        output_path=args.output,
    )
    metadata = payload["metadata"]
    print(f"Rows scanned: {metadata['rows_scanned']}")
    print(f"Rows used: {metadata['rows_used']}")
    print(f"Nutrition5k dishes: {metadata['dish_count']}")
    print(f"Unique normalized ingredients: {metadata['ingredient_count']}")
    print(f"Warnings: {metadata['warnings'] or 'none'}")
    print("Sample dishes:")
    for dish_id, dish in list(payload["nutrition5k_dishes"].items())[:5]:
        names = [item["ingredient"] for item in dish["ingredients"][:5]]
        print(f"  - {dish_id}: total_mass_g={dish['total_mass_g']} ingredients={names}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
