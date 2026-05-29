from __future__ import annotations

import argparse
import ast
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from food_ai.food_bridge.config import (
    DISH_PRIORS_PATH,
    FOOD101_IMAGE_DIR,
    MIN_RECIPE_MATCHES,
    TOP_INGREDIENTS_PER_DISH,
    VISION_CLASS_INDEX_PATH,
    resolve_recipe_nlg_path,
)
from food_ai.food_bridge.ingredient_normalize import normalize_ingredient


def _load_class_labels(class_index_path: Path = VISION_CLASS_INDEX_PATH, image_dir: Path = FOOD101_IMAGE_DIR) -> list[str]:
    if class_index_path.exists():
        payload = json.loads(class_index_path.read_text(encoding="utf-8"))
        classes = payload.get("classes")
        if isinstance(classes, list) and classes:
            return [str(label) for label in classes]
        idx_to_class = payload.get("idx_to_class")
        if isinstance(idx_to_class, dict) and idx_to_class:
            return [str(label) for _, label in sorted(((int(index), label) for index, label in idx_to_class.items()))]
    if image_dir.exists():
        return sorted(path.name for path in image_dir.iterdir() if path.is_dir())
    return []


def _dish_tokens(label: str) -> set[str]:
    return {token for token in label.lower().replace("_", " ").split() if token}


def _label_matchers(labels: list[str]) -> list[tuple[str, str, set[str]]]:
    return [(label, label.lower().replace("_", " "), _dish_tokens(label)) for label in labels]


def _title_matches_dish(title_text: str, title_tokens: set[str], label_phrase: str, label_tokens: set[str]) -> bool:
    if f" {label_phrase} " in title_text:
        return True
    if not label_tokens:
        return False
    return label_tokens.issubset(title_tokens)


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


def _iter_recipe_ingredients(row: dict[str, Any]) -> Iterable[str]:
    ner = _parse_list_field(row.get("NER"))
    if ner:
        yield from ner
        return
    yield from _parse_list_field(row.get("ingredients"))


def build_dish_priors(
    recipe_nlg_path: Path,
    output_path: Path = DISH_PRIORS_PATH,
    min_recipe_matches: int = MIN_RECIPE_MATCHES,
    top_ingredients_per_dish: int = TOP_INGREDIENTS_PER_DISH,
    class_labels: list[str] | None = None,
) -> dict[str, Any]:
    labels = class_labels or _load_class_labels()
    if not labels:
        raise ValueError("No Food-101 class labels found from class_index.json or food-101 image folders.")
    if not recipe_nlg_path.exists():
        raise FileNotFoundError(f"RecipeNLG CSV not found: {recipe_nlg_path}")

    label_matchers = _label_matchers(labels)
    counters: dict[str, Counter[str]] = {label: Counter() for label in labels}
    recipe_matches: Counter[str] = Counter()
    scanned_rows = 0

    with recipe_nlg_path.open("r", encoding="utf-8", newline="", errors="ignore") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            scanned_rows += 1
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
            ingredients = [normalize_ingredient(raw) for raw in _iter_recipe_ingredients(row)]
            normalized_ingredients = [ingredient for ingredient in ingredients if ingredient]
            if not normalized_ingredients:
                continue
            for label in matched_labels:
                recipe_matches[label] += 1
                counters[label].update(normalized_ingredients)

    priors: dict[str, list[dict[str, Any]]] = {}
    for label in labels:
        if recipe_matches[label] < min_recipe_matches:
            continue
        top_items = counters[label].most_common(top_ingredients_per_dish)
        total = sum(count for _, count in top_items)
        if total <= 0:
            continue
        priors[label] = [
            {
                "ingredient": ingredient,
                "weight": round(count / total, 6),
                "source_count": int(count),
            }
            for ingredient, count in top_items
        ]

    missing_labels = [label for label in labels if label not in priors]
    payload = {
        "metadata": {
            "source": "RecipeNLG",
            "source_path": str(recipe_nlg_path),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "recipe_rows_scanned": scanned_rows,
            "dish_count": len(labels),
            "dish_labels_with_priors": len(priors),
            "dish_labels_missing_priors": len(missing_labels),
            "min_recipe_matches": int(min_recipe_matches),
            "top_ingredients_per_dish": int(top_ingredients_per_dish),
        },
        "dish_priors": priors,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build dish-to-ingredient priors from RecipeNLG.")
    parser.add_argument("--recipe-nlg", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DISH_PRIORS_PATH)
    parser.add_argument("--min-recipe-matches", type=int, default=MIN_RECIPE_MATCHES)
    parser.add_argument("--top-ingredients-per-dish", type=int, default=TOP_INGREDIENTS_PER_DISH)
    args = parser.parse_args()

    recipe_path = resolve_recipe_nlg_path(args.recipe_nlg)
    labels = _load_class_labels()
    payload = build_dish_priors(
        recipe_path,
        output_path=args.output,
        min_recipe_matches=args.min_recipe_matches,
        top_ingredients_per_dish=args.top_ingredients_per_dish,
        class_labels=labels,
    )
    metadata = payload["metadata"]
    priors = payload["dish_priors"]
    missing = [label for label in labels if label not in priors]

    print("PALM Food Bridge Dish Prior Build")
    print("=" * 35)
    print(f"Recipe rows scanned: {metadata['recipe_rows_scanned']}")
    print(f"Dish labels found: {len(labels)}")
    print(f"Dish labels with priors: {metadata['dish_labels_with_priors']}")
    print(f"Dish labels missing priors: {metadata['dish_labels_missing_priors']}")
    if missing:
        print("Missing priors:")
        for label in missing[:25]:
            print(f"  - {label}")
    print("Sample priors:")
    for label in list(priors.keys())[:5]:
        print(f"  {label}: {priors[label][:5]}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
