from __future__ import annotations

import argparse
import ast
import csv
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from food_ai.food_bridge.build_dish_priors import _label_matchers, _load_class_labels, _title_matches_dish
from food_ai.food_bridge.config import DB_PATH, DISH_MASS_PRIORS_PATH, MIN_RECIPE_MATCHES, TOP_INGREDIENTS_PER_DISH, resolve_recipe_nlg_path
from food_ai.food_bridge.ingredient_measure_parser import ParsedIngredientMeasure, parse_ingredient_measure
from food_ai.food_bridge.ingredient_normalize import normalize_ingredient
from food_ai.food_bridge.nutrition_bridge import LOCAL_NUTRITION_ALIASES
from food_ai.food_bridge.usda_portions import lookup_portion_grams


DIRECT_MASS_UNITS = {
    "gram": 1.0,
    "kilogram": 1000.0,
    "ounce": 28.349523125,
    "pound": 453.59237,
}
MAX_REASONABLE_INGREDIENT_GRAMS = 5000.0


def _parse_list_field(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(parsed, (list, tuple)):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [str(parsed).strip()] if str(parsed).strip() else []


def _safe_hardcoded_grams(parsed: ParsedIngredientMeasure) -> tuple[float | None, str | None]:
    quantity = parsed.quantity if parsed.quantity is not None else 1.0
    ingredient = parsed.normalized_ingredient or ""
    unit = parsed.unit
    rules = {
        ("egg", None): 50.0,
        ("egg", "piece"): 50.0,
        ("butter", "stick"): 113.0,
        ("butter", "tablespoon"): 14.0,
        ("oil", "tablespoon"): 13.5,
        ("olive oil", "tablespoon"): 13.5,
        ("salt", "teaspoon"): 6.0,
        ("milk", "cup"): 244.0,
        ("wheat flour", "cup"): 120.0,
        ("sugar", "cup"): 200.0,
        ("garlic", "clove"): 3.0,
    }
    grams_per_unit = rules.get((ingredient, unit))
    if grams_per_unit is None:
        return None, None
    return round(grams_per_unit * quantity, 6), "hardcoded_safe"


def _load_ingredient_fdc_map(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        return {}
    mapping: dict[str, int] = {}
    with closing(sqlite3.connect(db_path)) as connection:
        table_exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='generic_ingredient_nutrition'"
        ).fetchone()
        if table_exists is None:
            return {}
        for ingredient, fdc_id in connection.execute(
            "SELECT ingredient, fdc_id FROM generic_ingredient_nutrition WHERE fdc_id IS NOT NULL"
        ):
            normalized = normalize_ingredient(str(ingredient or ""))
            if normalized:
                mapping.setdefault(normalized, int(fdc_id))
    return mapping


def _resolve_fdc_id(ingredient: str | None, fdc_map: dict[str, int]) -> int | None:
    if not ingredient:
        return None
    normalized = normalize_ingredient(ingredient) or ingredient
    if normalized in fdc_map:
        return fdc_map[normalized]
    for alias in LOCAL_NUTRITION_ALIASES.get(normalized, []):
        alias_normalized = normalize_ingredient(alias) or alias
        if alias_normalized in fdc_map:
            return fdc_map[alias_normalized]
    return None


def _convert_to_grams(
    parsed: ParsedIngredientMeasure,
    db_path: Path,
    fdc_map: dict[str, int],
    portion_cache: dict[tuple[int, str, float], dict[str, Any]],
) -> dict[str, Any]:
    quantity = parsed.quantity if parsed.quantity is not None else 1.0
    if parsed.unit in DIRECT_MASS_UNITS and parsed.quantity is not None:
        return {
            "status": "converted",
            "grams": round(DIRECT_MASS_UNITS[parsed.unit] * parsed.quantity, 6),
            "conversion_source": "direct_mass",
            "fdc_id": None,
            "portion_lookup": None,
        }

    normalized = parsed.normalized_ingredient
    if normalized:
        fdc_id = _resolve_fdc_id(normalized, fdc_map)
        if fdc_id is not None and parsed.unit:
            cache_key = (int(fdc_id), parsed.unit, round(float(quantity), 6))
            portion = portion_cache.get(cache_key)
            if portion is None:
                portion = lookup_portion_grams(db_path, int(fdc_id), parsed.unit, quantity)
                portion_cache[cache_key] = portion
            if portion.get("status") == "found":
                return {
                    "status": "converted",
                    "grams": portion.get("grams"),
                    "conversion_source": "usda_food_portion",
                    "fdc_id": fdc_id,
                    "portion_lookup": portion,
                }

    safe_grams, source = _safe_hardcoded_grams(parsed)
    if safe_grams is not None:
        return {
            "status": "converted",
            "grams": safe_grams,
            "conversion_source": source,
            "fdc_id": None,
            "portion_lookup": None,
        }
    return {
        "status": "unconverted",
        "grams": None,
        "conversion_source": "unconverted",
        "fdc_id": None,
        "portion_lookup": None,
    }


def build_dish_mass_priors(
    recipe_nlg_path: Path,
    db_path: Path = DB_PATH,
    output_path: Path = DISH_MASS_PRIORS_PATH,
    min_recipe_matches: int = MIN_RECIPE_MATCHES,
    top_ingredients_per_dish: int = TOP_INGREDIENTS_PER_DISH,
    class_labels: list[str] | None = None,
    max_rows: int | None = None,
) -> dict[str, Any]:
    labels = class_labels or _load_class_labels()
    if not labels:
        raise ValueError("No Food-101 class labels found from class_index.json or food-101 image folders.")
    if not recipe_nlg_path.exists():
        raise FileNotFoundError(f"RecipeNLG CSV not found: {recipe_nlg_path}")

    label_matchers = _label_matchers(labels)
    recipe_counts: Counter[str] = Counter()
    converted_recipe_counts: Counter[str] = Counter()
    ingredient_grams: dict[str, dict[str, list[float]]] = {label: defaultdict(list) for label in labels}
    source_counts: dict[str, Counter[str]] = {label: Counter() for label in labels}
    conversion_summary: dict[str, Counter[str]] = {label: Counter() for label in labels}
    fdc_map = _load_ingredient_fdc_map(db_path)
    portion_cache: dict[tuple[int, str, float], dict[str, Any]] = {}
    scanned_rows = 0

    with recipe_nlg_path.open("r", encoding="utf-8", newline="", errors="ignore") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            scanned_rows += 1
            if max_rows is not None and scanned_rows > max_rows:
                break
            title = str(row.get("title") or "")
            if not title:
                continue
            title_text = f" {title.lower().replace('-', ' ')} "
            title_tokens = set(title_text.split())
            matched_labels = [
                label
                for label, label_phrase, label_tokens in label_matchers
                if label_tokens.intersection(title_tokens) and _title_matches_dish(title_text, title_tokens, label_phrase, label_tokens)
            ]
            if not matched_labels:
                continue

            recipe_ingredients = _parse_list_field(row.get("ingredients"))
            ner_ingredients = _parse_list_field(row.get("NER"))
            if not recipe_ingredients:
                continue
            parsed_rows = [parse_ingredient_measure(line) for line in recipe_ingredients]
            if len(ner_ingredients) == len(parsed_rows):
                for parsed, ner_ingredient in zip(parsed_rows, ner_ingredients):
                    normalized_ner = normalize_ingredient(ner_ingredient)
                    if normalized_ner:
                        parsed.normalized_ingredient = normalized_ner
            conversions = [(parsed, _convert_to_grams(parsed, db_path, fdc_map, portion_cache)) for parsed in parsed_rows if parsed.normalized_ingredient]
            converted = [
                (parsed, conversion)
                for parsed, conversion in conversions
                if conversion.get("status") == "converted"
                and conversion.get("grams")
                and float(conversion["grams"]) <= MAX_REASONABLE_INGREDIENT_GRAMS
            ]
            recipe_total_grams = sum(float(conversion["grams"]) for _, conversion in converted)

            for label in matched_labels:
                recipe_counts[label] += 1
                for _, conversion in conversions:
                    conversion_summary[label][str(conversion.get("conversion_source") or "unconverted")] += 1
                if recipe_total_grams <= 0:
                    continue
                converted_recipe_counts[label] += 1
                for parsed, conversion in converted:
                    ingredient = str(parsed.normalized_ingredient)
                    grams = float(conversion["grams"])
                    ingredient_grams[label][ingredient].append(grams)
                    source_counts[label][ingredient] += 1

    dish_mass_priors: dict[str, Any] = {}
    for label in labels:
        recipe_count = recipe_counts[label]
        converted_recipe_count = converted_recipe_counts[label]
        if recipe_count < min_recipe_matches or converted_recipe_count <= 0:
            continue
        ingredient_medians = {
            ingredient: statistics.median(values)
            for ingredient, values in ingredient_grams[label].items()
            if len(values) >= min_recipe_matches
        }
        total_median = sum(ingredient_medians.values())
        if total_median <= 0:
            continue
        top_items = sorted(ingredient_medians.items(), key=lambda item: item[1], reverse=True)[:top_ingredients_per_dish]
        top_total = sum(value for _, value in top_items)
        dish_mass_priors[label] = {
            "recipe_count": int(recipe_count),
            "converted_recipe_count": int(converted_recipe_count),
            "coverage": round(converted_recipe_count / max(1, recipe_count), 6),
            "conversion_source_summary": dict(conversion_summary[label]),
            "ingredients": [
                {
                    "ingredient": ingredient,
                    "mass_fraction": round(median_grams / top_total, 6),
                    "source_count": int(source_counts[label][ingredient]),
                    "median_grams": round(median_grams, 3),
                }
                for ingredient, median_grams in top_items
            ],
        }

    payload = {
        "metadata": {
            "source": "RecipeNLG measurements + USDA food_portion/measure_unit",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "recipe_rows_scanned": scanned_rows,
            "max_rows": max_rows,
            "dish_count": len(labels),
            "dish_labels_with_mass_priors": len(dish_mass_priors),
            "min_recipe_matches": int(min_recipe_matches),
            "top_ingredients_per_dish": int(top_ingredients_per_dish),
            "note": "Built offline. Runtime must not scan RecipeNLG or USDA CSVs.",
        },
        "dish_mass_priors": dish_mass_priors,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build RecipeNLG measurement mass priors using USDA portion tables.")
    parser.add_argument("--recipe-nlg", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--output", type=Path, default=DISH_MASS_PRIORS_PATH)
    parser.add_argument("--min-recipe-matches", type=int, default=MIN_RECIPE_MATCHES)
    parser.add_argument("--top-ingredients-per-dish", type=int, default=TOP_INGREDIENTS_PER_DISH)
    parser.add_argument("--max-rows", type=int, default=None, help="Optional smoke-test limit; omit for full offline build.")
    args = parser.parse_args()

    payload = build_dish_mass_priors(
        recipe_nlg_path=resolve_recipe_nlg_path(args.recipe_nlg),
        db_path=args.db_path,
        output_path=args.output,
        min_recipe_matches=args.min_recipe_matches,
        top_ingredients_per_dish=args.top_ingredients_per_dish,
        max_rows=args.max_rows,
    )
    metadata = payload["metadata"]
    print("PALM Dish Mass Prior Build")
    print("=" * 27)
    print(f"Recipe rows scanned: {metadata['recipe_rows_scanned']}")
    print(f"Dish labels with mass priors: {metadata['dish_labels_with_mass_priors']}")
    print("Sample mass priors:")
    for label, prior in list(payload["dish_mass_priors"].items())[:5]:
        print(f"  {label}: coverage={prior['coverage']} ingredients={prior['ingredients'][:5]}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
