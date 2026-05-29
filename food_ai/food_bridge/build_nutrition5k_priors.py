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
from food_ai.food_bridge.nutrition5k_loader import inspect_csv


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _round_or_none(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _load_ingredient_metadata(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    metadata: dict[str, dict[str, Any]] = {}
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ingredient_id = str(row.get("ingr_id") or "").strip()
            if not ingredient_id:
                continue
            metadata[ingredient_id] = dict(row)
    return metadata


def _load_dish_nutrition(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    nutrition: dict[str, dict[str, Any]] = {}
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            dish_id = str(row.get("dish_id") or "").strip()
            if not dish_id:
                continue
            mass = _as_float(row.get("mass"))
            calories = _as_float(row.get("calories"))
            fat = _as_float(row.get("fat"))
            carb = _as_float(row.get("carb"))
            protein = _as_float(row.get("protein"))
            nutrition[dish_id] = {
                "mass": mass,
                "nutrition_per_100g": {
                    "calories": _round_or_none(calories / mass * 100.0, 3) if calories is not None and mass and mass > 0 else None,
                    "protein_g": _round_or_none(protein / mass * 100.0, 3) if protein is not None and mass and mass > 0 else None,
                    "total_fat_g": _round_or_none(fat / mass * 100.0, 3) if fat is not None and mass and mass > 0 else None,
                    "total_carbs_g": _round_or_none(carb / mass * 100.0, 3) if carb is not None and mass and mass > 0 else None,
                    "fiber_g": None,
                    "total_sugars_g": None,
                    "sodium_mg": None,
                },
            }
    return nutrition


def build_nutrition5k_priors(
    dish_ingredients_path: Path = NUTRITION5K_DISH_INGREDIENTS_PATH,
    dish_nutrition_path: Path = NUTRITION5K_DISH_NUTRITION_PATH,
    ingredients_metadata_path: Path = NUTRITION5K_INGREDIENTS_METADATA_PATH,
    output_path: Path = NUTRITION5K_MASS_PRIORS_PATH,
    min_hint_dish_count: int = 5,
) -> dict[str, Any]:
    if not dish_ingredients_path.exists():
        raise FileNotFoundError(f"Nutrition5k dish ingredients CSV not found: {dish_ingredients_path}")

    metadata_by_id = _load_ingredient_metadata(ingredients_metadata_path)
    dish_nutrition = _load_dish_nutrition(dish_nutrition_path)
    dishes_raw: dict[str, dict[str, Any]] = defaultdict(lambda: {"ingredients": [], "ingredient_mass_total": 0.0})

    with dish_ingredients_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            dish_id = str(row.get("dish_id") or "").strip()
            ingredient_id = str(row.get("ingr_id") or "").strip()
            raw_name = str(row.get("ingr_name") or "").strip()
            grams = _as_float(row.get("grams"))
            if not dish_id or grams is None or grams <= 0.0:
                continue
            metadata = metadata_by_id.get(ingredient_id, {})
            ingredient_name = raw_name or str(metadata.get("ingr_name") or "").strip()
            normalized = normalize_ingredient(ingredient_name)
            if not normalized:
                continue
            dish = dishes_raw[dish_id]
            dish["ingredient_mass_total"] += grams
            dish["ingredients"].append(
                {
                    "ingredient": normalized,
                    "raw_ingredient": ingredient_name,
                    "ingredient_id": ingredient_id or None,
                    "mass_g": grams,
                    "nutrition_source": "nutrition5k",
                }
            )

    nutrition5k_dishes: dict[str, Any] = {}
    hint_accumulator: dict[str, dict[str, Any]] = defaultdict(lambda: {"mass_fraction_total": 0.0, "mass_g_total": 0.0, "dish_count": 0})
    for dish_id, dish in sorted(dishes_raw.items()):
        dish_mass = _as_float((dish_nutrition.get(dish_id) or {}).get("mass"))
        ingredient_mass_total = float(dish["ingredient_mass_total"])
        total_mass = dish_mass if dish_mass and dish_mass > 0.0 else ingredient_mass_total
        if total_mass <= 0.0:
            continue
        ingredients = []
        seen_in_dish: set[str] = set()
        for ingredient in dish["ingredients"]:
            mass_g = float(ingredient["mass_g"])
            mass_fraction = mass_g / total_mass
            ingredients.append(
                {
                    **ingredient,
                    "mass_g": round(mass_g, 6),
                    "mass_fraction": round(mass_fraction, 6),
                }
            )
            name = str(ingredient["ingredient"])
            if name not in seen_in_dish:
                hint_accumulator[name]["dish_count"] += 1
                seen_in_dish.add(name)
            hint_accumulator[name]["mass_fraction_total"] += mass_fraction
            hint_accumulator[name]["mass_g_total"] += mass_g
        nutrition5k_dishes[dish_id] = {
            "total_mass_g": round(total_mass, 6),
            "nutrition_per_100g": (dish_nutrition.get(dish_id) or {}).get("nutrition_per_100g"),
            "ingredients": sorted(ingredients, key=lambda item: item["mass_fraction"], reverse=True),
        }

    ingredient_mass_hints = {}
    for ingredient, values in sorted(hint_accumulator.items()):
        dish_count = int(values["dish_count"])
        if dish_count < min_hint_dish_count:
            continue
        ingredient_mass_hints[ingredient] = {
            "avg_mass_fraction": round(float(values["mass_fraction_total"]) / dish_count, 6),
            "avg_mass_g": round(float(values["mass_g_total"]) / dish_count, 6),
            "dish_count": dish_count,
            "source": "nutrition5k",
        }

    payload = {
        "metadata": {
            "source": "Nutrition5k simplified CSVs",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dish_count": len(nutrition5k_dishes),
            "ingredient_count": len(metadata_by_id),
            "ingredient_mass_hint_count": len(ingredient_mass_hints),
            "min_hint_dish_count": int(min_hint_dish_count),
            "note": "Nutrition5k dish IDs are not Food-101 dish labels. Runtime uses ingredient-level mass hints only when explicitly enabled.",
            "input_files": {
                "dish_ingredients": str(dish_ingredients_path),
                "dish_nutrition_values": str(dish_nutrition_path),
                "ingredients_metadata": str(ingredients_metadata_path),
            },
        },
        "ingredient_mass_hints": ingredient_mass_hints,
        "nutrition5k_dishes": nutrition5k_dishes,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build optional Nutrition5k mass-hint priors for PALM food bridge.")
    parser.add_argument("--dish-ingredients", type=Path, default=NUTRITION5K_DISH_INGREDIENTS_PATH)
    parser.add_argument("--dish-nutrition", type=Path, default=NUTRITION5K_DISH_NUTRITION_PATH)
    parser.add_argument("--ingredients-metadata", type=Path, default=NUTRITION5K_INGREDIENTS_METADATA_PATH)
    parser.add_argument("--output", type=Path, default=NUTRITION5K_MASS_PRIORS_PATH)
    parser.add_argument("--min-hint-dish-count", type=int, default=5)
    args = parser.parse_args()

    print("PALM Nutrition5k Prior Build")
    print("=" * 28)
    for label, path in [
        ("dish ingredients", args.dish_ingredients),
        ("dish nutrition", args.dish_nutrition),
        ("ingredient metadata", args.ingredients_metadata),
    ]:
        info = inspect_csv(path, sample_size=1)
        print(f"{label}: exists={info['exists']} rows={info['row_count']} columns={info['columns']}")

    payload = build_nutrition5k_priors(
        dish_ingredients_path=args.dish_ingredients,
        dish_nutrition_path=args.dish_nutrition,
        ingredients_metadata_path=args.ingredients_metadata,
        output_path=args.output,
        min_hint_dish_count=max(1, args.min_hint_dish_count),
    )
    metadata = payload["metadata"]
    print(f"Nutrition5k dishes: {metadata['dish_count']}")
    print(f"Ingredient metadata rows: {metadata['ingredient_count']}")
    print(f"Ingredient mass hints: {metadata['ingredient_mass_hint_count']}")
    print("Sample mass hints:")
    for ingredient, hint in list(payload["ingredient_mass_hints"].items())[:10]:
        print(f"  - {ingredient}: {hint}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
