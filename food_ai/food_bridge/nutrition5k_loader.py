from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from food_ai.food_bridge.config import (
    NUTRITION5K_DISH_INGREDIENTS_PATH,
    NUTRITION5K_DISH_NUTRITION_PATH,
    NUTRITION5K_INGREDIENTS_METADATA_PATH,
)


NUTRIENT_COLUMN_CANDIDATES = {
    "calories": ("calories", "calorie", "cal/g", "kcal"),
    "protein": ("protein", "protein_g", "protein(g)"),
    "fat": ("fat", "fat_g", "fat(g)"),
    "carb": ("carb", "carbs", "carbs_g", "carb(g)", "total_carbs_g"),
}

REQUIRED_DISH_INGREDIENT_COLUMNS = ("dish_id", "ingredient_name", "gram_or_mass")
REQUIRED_DISH_NUTRITION_COLUMNS = ("dish_id", "gram_or_mass")


def _count_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return sum(1 for _ in reader)


def _pick_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {column.lower(): column for column in columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def _detect_columns(columns: list[str]) -> dict[str, Any]:
    return {
        "dish_id": _pick_column(columns, ("dish_id", "dish", "id")),
        "ingredient_id": _pick_column(columns, ("ingr_id", "ingredient_id", "food_id")),
        "ingredient_name": _pick_column(columns, ("ingr_name", "ingredient", "ingredient_name", "name")),
        "gram_or_mass": _pick_column(columns, ("grams", "gram", "mass", "mass_g", "weight_g")),
        "nutrition": {
            key: _pick_column(columns, candidates)
            for key, candidates in NUTRIENT_COLUMN_CANDIDATES.items()
        },
    }


def inspect_csv(path: Path, sample_size: int = 3) -> dict[str, Any]:
    info: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "row_count": None,
        "columns": [],
        "sample_rows": [],
        "detected": {},
    }
    if not path.exists():
        return info
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        info["columns"] = columns
        info["detected"] = _detect_columns(columns)
        for _, row in zip(range(sample_size), reader):
            info["sample_rows"].append(dict(row))
    info["row_count"] = _count_rows(path)
    return info


def inspect_nutrition5k(
    dish_ingredients_path: Path = NUTRITION5K_DISH_INGREDIENTS_PATH,
    dish_nutrition_path: Path = NUTRITION5K_DISH_NUTRITION_PATH,
    ingredients_metadata_path: Path = NUTRITION5K_INGREDIENTS_METADATA_PATH,
    sample_size: int = 3,
) -> dict[str, Any]:
    return {
        "dish_ingredients": inspect_csv(dish_ingredients_path, sample_size),
        "dish_nutrition_values": inspect_csv(dish_nutrition_path, sample_size),
        "ingredients_metadata": inspect_csv(ingredients_metadata_path, sample_size),
    }


def detect_nutrition5k_schema(
    dish_ingredients_path: Path = NUTRITION5K_DISH_INGREDIENTS_PATH,
    dish_nutrition_path: Path = NUTRITION5K_DISH_NUTRITION_PATH,
    ingredients_metadata_path: Path = NUTRITION5K_INGREDIENTS_METADATA_PATH,
) -> dict[str, Any]:
    report = inspect_nutrition5k(
        dish_ingredients_path=dish_ingredients_path,
        dish_nutrition_path=dish_nutrition_path,
        ingredients_metadata_path=ingredients_metadata_path,
        sample_size=2,
    )
    errors: list[str] = []
    warnings: list[str] = []

    dish_ingredients = report["dish_ingredients"]
    if not dish_ingredients["exists"]:
        errors.append(f"dish_ingredients_missing:{dish_ingredients_path}")
    else:
        detected = dish_ingredients.get("detected") or {}
        missing = [name for name in REQUIRED_DISH_INGREDIENT_COLUMNS if not detected.get(name)]
        if missing:
            errors.append(f"dish_ingredients_missing_required_columns:{','.join(missing)}")

    dish_nutrition = report["dish_nutrition_values"]
    if not dish_nutrition["exists"]:
        warnings.append(f"dish_nutrition_values_missing:{dish_nutrition_path}")
    else:
        detected = dish_nutrition.get("detected") or {}
        missing = [name for name in REQUIRED_DISH_NUTRITION_COLUMNS if not detected.get(name)]
        if missing:
            warnings.append(f"dish_nutrition_values_missing_columns:{','.join(missing)}")
        nutrition = detected.get("nutrition") or {}
        missing_nutrients = [key for key in ("calories", "protein", "fat", "carb") if not nutrition.get(key)]
        if missing_nutrients:
            warnings.append(f"dish_nutrition_values_missing_nutrients:{','.join(missing_nutrients)}")

    ingredient_metadata = report["ingredients_metadata"]
    if not ingredient_metadata["exists"]:
        warnings.append(f"ingredients_metadata_missing:{ingredients_metadata_path}")
    else:
        detected = ingredient_metadata.get("detected") or {}
        if not detected.get("ingredient_id") or not detected.get("ingredient_name"):
            warnings.append("ingredients_metadata_missing_join_columns")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "files": report,
        "detected": {
            name: info.get("detected") or {}
            for name, info in report.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect local Nutrition5k simplified CSV metadata.")
    parser.add_argument("--dish-ingredients", type=Path, default=NUTRITION5K_DISH_INGREDIENTS_PATH)
    parser.add_argument("--dish-nutrition", type=Path, default=NUTRITION5K_DISH_NUTRITION_PATH)
    parser.add_argument("--ingredients-metadata", type=Path, default=NUTRITION5K_INGREDIENTS_METADATA_PATH)
    parser.add_argument("--sample-size", type=int, default=3)
    args = parser.parse_args()

    report = inspect_nutrition5k(
        dish_ingredients_path=args.dish_ingredients,
        dish_nutrition_path=args.dish_nutrition,
        ingredients_metadata_path=args.ingredients_metadata,
        sample_size=max(0, args.sample_size),
    )
    schema = detect_nutrition5k_schema(
        dish_ingredients_path=args.dish_ingredients,
        dish_nutrition_path=args.dish_nutrition,
        ingredients_metadata_path=args.ingredients_metadata,
    )
    print("PALM Nutrition5k CSV Inspection")
    print("=" * 31)
    for name, info in report.items():
        print(f"\n{name}")
        print("-" * len(name))
        print(f"file exists: {'yes' if info['exists'] else 'no'}")
        print(f"path: {info['path']}")
        print(f"row count: {info['row_count'] if info['row_count'] is not None else 'unavailable'}")
        print(f"columns: {', '.join(info['columns']) if info['columns'] else 'none'}")
        print(f"detected dish id column: {info['detected'].get('dish_id')}")
        print(f"detected ingredient id column: {info['detected'].get('ingredient_id')}")
        print(f"detected ingredient name column: {info['detected'].get('ingredient_name')}")
        print(f"detected gram/mass column: {info['detected'].get('gram_or_mass')}")
        print(f"detected calorie/protein/fat/carb columns: {info['detected'].get('nutrition')}")
        print("sample rows:")
        for row in info["sample_rows"]:
            print(json.dumps(row, ensure_ascii=False)[:1200])
    print("\nschema status")
    print("-------------")
    print(f"ok: {schema['ok']}")
    print(f"errors: {schema['errors'] or 'none'}")
    print(f"warnings: {schema['warnings'] or 'none'}")


if __name__ == "__main__":
    main()
