from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from food_ai.food_bridge.build_dish_mass_priors import (
    MAX_REASONABLE_INGREDIENT_GRAMS,
    _convert_to_grams,
    _load_ingredient_fdc_map,
    _parse_list_field,
)
from food_ai.food_bridge.build_dish_priors import _label_matchers, _load_class_labels, _title_matches_dish
from food_ai.food_bridge.config import DB_PATH, DISH_MASS_PRIORS_PATH, DISH_PRIORS_PATH, resolve_recipe_nlg_path
from food_ai.food_bridge.ingredient_measure_parser import parse_ingredient_measure
from food_ai.food_bridge.ingredient_normalize import normalize_ingredient
from food_ai.food_bridge.migrate_dish_ingredient_priors import run_migration
from food_ai.food_bridge.nutrition_bridge import lookup_ingredient_nutrition


RAW_SOURCE = "recipenlg_raw_measurements_usda_portions"
PRIOR_SOURCE = "recipe_artifact_mass_priors"
FREQUENCY_SOURCE = "recipe_artifact_frequency_priors"
GRAMS_PER_100G_CAPS = {
    "baking powder": 2.0,
    "baking soda": 0.3,
    "cinnamon": 0.4,
    "nutmeg": 0.5,
    "oregano": 0.5,
    "pepper": 0.5,
    "salt": 0.7,
    "vanilla": 0.4,
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dish_ids(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        str(label): int(dish_id)
        for dish_id, label in connection.execute("SELECT dish_id, food101_label FROM dishes")
    }


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0.0 else None


def _portion_method(mean: float | None, mode: float | None, median: float | None, observations: int) -> tuple[float | None, str]:
    if mean is not None and observations >= 2:
        return mean, "mean"
    if mode is not None:
        return mode, "mode_fallback"
    if median is not None:
        return median, "median_fallback"
    return None, "missing"


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 6) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def _mode(values: list[float]) -> float | None:
    if not values:
        return None
    rounded = [round(value, 3) for value in values]
    return float(Counter(rounded).most_common(1)[0][0])


def _std(values: list[float]) -> float | None:
    return round(statistics.stdev(values), 6) if len(values) > 1 else None


def _cap_grams_per_100g(ingredient: str, grams: float | None, method: str) -> tuple[float | None, str]:
    if grams is None:
        return None, method
    cap = GRAMS_PER_100G_CAPS.get(ingredient)
    if cap is None or grams <= cap:
        return grams, method
    return cap, f"{method}_capped"


def _nutrition_match(ingredient: str, db_path: Path) -> dict[str, Any]:
    result = lookup_ingredient_nutrition(ingredient, db_path)
    if result.get("status") == "found":
        return result
    return {
        "status": "not_found",
        "matched_ingredient": None,
        "fdc_id": None,
        "reason": result.get("reason"),
    }


def _upsert_prior(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO dish_ingredient_priors (
            dish_id, canonical_ingredient, ingredient_norm_key, fdc_id, usda_description, prep_state,
            grams_mean, grams_mode, grams_median, grams_std, grams_default, grams_per_100g_dish, portion_method,
            pct_recipes_present, ingredient_rank, n_recipes, n_weight_observations,
            default_include, confidence, source, updated_at
        )
        VALUES (
            :dish_id, :canonical_ingredient, :ingredient_norm_key, :fdc_id, :usda_description, :prep_state,
            :grams_mean, :grams_mode, :grams_median, :grams_std, :grams_default, :grams_per_100g_dish, :portion_method,
            :pct_recipes_present, :ingredient_rank, :n_recipes, :n_weight_observations,
            :default_include, :confidence, :source, CURRENT_TIMESTAMP
        )
        ON CONFLICT(dish_id, canonical_ingredient, fdc_id) DO UPDATE SET
            ingredient_norm_key = excluded.ingredient_norm_key,
            usda_description = excluded.usda_description,
            prep_state = excluded.prep_state,
            grams_mean = excluded.grams_mean,
            grams_mode = excluded.grams_mode,
            grams_median = excluded.grams_median,
            grams_std = excluded.grams_std,
            grams_default = excluded.grams_default,
            grams_per_100g_dish = excluded.grams_per_100g_dish,
            portion_method = excluded.portion_method,
            pct_recipes_present = excluded.pct_recipes_present,
            ingredient_rank = excluded.ingredient_rank,
            n_recipes = excluded.n_recipes,
            n_weight_observations = excluded.n_weight_observations,
            default_include = excluded.default_include,
            confidence = excluded.confidence,
            source = excluded.source,
            updated_at = CURRENT_TIMESTAMP
        """,
        row,
    )


def _set_grams_per_100g_dish(rows: list[dict[str, Any]]) -> None:
    total_default_grams = sum(float(row.get("grams_default") or 0.0) for row in rows)
    for row in rows:
        grams_default = _safe_float(row.get("grams_default"))
        row["grams_per_100g_dish"] = (
            round(grams_default / total_default_grams * 100.0, 6)
            if grams_default is not None and total_default_grams > 0.0
            else None
        )


def _nutrition_source_key(row: dict[str, Any]) -> str:
    fdc_id = row.get("fdc_id")
    if fdc_id not in {None, ""}:
        return f"fdc:{fdc_id}"
    matched = normalize_ingredient(str(row.get("usda_description") or ""))
    if matched:
        return f"usda:{matched}"
    return f"ingredient:{row.get('ingredient_norm_key') or row.get('canonical_ingredient')}"


def _collapse_rows_by_nutrition_source(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_nutrition_source_key(row)].append(row)

    collapsed: list[dict[str, Any]] = []
    for group_rows in grouped.values():
        if len(group_rows) == 1:
            collapsed.append(dict(group_rows[0]))
            continue
        representative = max(
            group_rows,
            key=lambda row: (
                float(row.get("grams_default") or 0.0),
                float(row.get("confidence") or 0.0),
                int(row.get("n_weight_observations") or 0),
            ),
        )
        item = dict(representative)
        item["canonical_ingredient"] = (
            normalize_ingredient(str(representative.get("usda_description") or ""))
            or representative.get("canonical_ingredient")
        )
        item["ingredient_norm_key"] = normalize_ingredient(str(item["canonical_ingredient"])) or item["canonical_ingredient"]
        item["pct_recipes_present"] = max(float(row.get("pct_recipes_present") or 0.0) for row in group_rows)
        item["n_weight_observations"] = sum(int(row.get("n_weight_observations") or 0) for row in group_rows) or None
        item["confidence"] = max(float(row.get("confidence") or 0.0) for row in group_rows)
        item["default_include"] = 1 if item.get("fdc_id") is not None and item.get("grams_default") else 0
        item["portion_method"] = f"{representative.get('portion_method')}_collapsed_by_fdc"
        collapsed.append(item)
    return collapsed


def _clear_derived_rows(connection: sqlite3.Connection) -> None:
    sources = (RAW_SOURCE, PRIOR_SOURCE, FREQUENCY_SOURCE)
    connection.execute(
        f"DELETE FROM dish_ingredient_priors WHERE source IN ({', '.join(['?'] * len(sources))})",
        sources,
    )
    connection.execute(
        f"DELETE FROM dish_portion_priors WHERE source IN ({', '.join(['?'] * len(sources))})",
        sources,
    )


def _clear_raw_observations(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM recipe_ingredient_observations WHERE recipe_source = ?", (RAW_SOURCE,))


def _insert_observation(connection: sqlite3.Connection, observation: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO recipe_ingredient_observations (
            dish_id, recipe_id, recipe_source, raw_ingredient_text, canonical_ingredient,
            ingredient_norm_key, parsed_quantity, parsed_unit, parsed_grams, servings,
            grams_per_serving, fdc_id, usda_description, parse_confidence,
            match_confidence, created_at
        )
        VALUES (
            :dish_id, :recipe_id, :recipe_source, :raw_ingredient_text, :canonical_ingredient,
            :ingredient_norm_key, :parsed_quantity, :parsed_unit, :parsed_grams, :servings,
            :grams_per_serving, :fdc_id, :usda_description, :parse_confidence,
            :match_confidence, CURRENT_TIMESTAMP
        )
        """,
        observation,
    )


def _build_db_priors_from_raw_recipes(
    *,
    connection: sqlite3.Connection,
    db_path: Path,
    recipe_nlg_path: Path,
    top_ingredients_per_dish: int,
    min_weight_observations: int,
    max_rows: int | None,
    dish_filter: set[str] | None,
    store_observations: bool,
) -> dict[str, Any]:
    labels = _load_class_labels()
    if dish_filter:
        labels = [label for label in labels if label in dish_filter]
    ids_by_label = _dish_ids(connection)
    labels = [label for label in labels if label in ids_by_label]
    if not labels:
        raise ValueError("No Food-101 labels matched the requested raw RecipeNLG DB build.")
    if not recipe_nlg_path.exists():
        raise FileNotFoundError(f"RecipeNLG CSV not found: {recipe_nlg_path}")

    label_matchers = _label_matchers(labels)
    fdc_map = _load_ingredient_fdc_map(db_path)
    portion_cache: dict[tuple[int, str, float], dict[str, Any]] = {}
    nutrition_cache: dict[str, dict[str, Any]] = {}
    recipe_counts: Counter[str] = Counter()
    converted_recipe_counts: Counter[str] = Counter()
    source_counts: dict[str, Counter[str]] = {label: Counter() for label in labels}
    grams_by_label: dict[str, dict[str, list[float]]] = {label: defaultdict(list) for label in labels}
    recipe_total_grams: dict[str, list[float]] = {label: [] for label in labels}
    scanned_rows = 0
    matched_recipe_rows = 0
    observations_inserted = 0
    committed_observations = 0

    if store_observations:
        _clear_raw_observations(connection)
        connection.commit()

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
                if label_tokens.intersection(title_tokens)
                and _title_matches_dish(title_text, title_tokens, label_phrase, label_tokens)
            ]
            if not matched_labels:
                continue

            recipe_ingredients = _parse_list_field(row.get("ingredients"))
            if not recipe_ingredients:
                continue
            matched_recipe_rows += 1
            parsed_rows = [parse_ingredient_measure(line) for line in recipe_ingredients]
            ner_ingredients = _parse_list_field(row.get("NER"))
            if len(ner_ingredients) == len(parsed_rows):
                for parsed, ner_ingredient in zip(parsed_rows, ner_ingredients):
                    normalized_ner = normalize_ingredient(ner_ingredient)
                    if normalized_ner:
                        parsed.normalized_ingredient = normalized_ner

            conversions = [
                (parsed, _convert_to_grams(parsed, db_path, fdc_map, portion_cache))
                for parsed in parsed_rows
                if parsed.normalized_ingredient
            ]
            converted = [
                (parsed, conversion)
                for parsed, conversion in conversions
                if conversion.get("status") == "converted"
                and conversion.get("grams")
                and float(conversion["grams"]) <= MAX_REASONABLE_INGREDIENT_GRAMS
            ]
            total_grams = sum(float(conversion["grams"]) for _, conversion in converted)
            ingredients_seen = {str(parsed.normalized_ingredient) for parsed, _ in converted if parsed.normalized_ingredient}
            recipe_id = str(row.get("id") or row.get("recipe_id") or scanned_rows)

            for label in matched_labels:
                dish_id = ids_by_label[label]
                recipe_counts[label] += 1
                if total_grams > 0.0:
                    converted_recipe_counts[label] += 1
                    recipe_total_grams[label].append(total_grams)
                for ingredient in ingredients_seen:
                    source_counts[label][ingredient] += 1
                for parsed, conversion in converted:
                    ingredient = str(parsed.normalized_ingredient)
                    grams = float(conversion["grams"])
                    grams_by_label[label][ingredient].append(grams)
                    if store_observations:
                        _insert_observation(
                            connection,
                            {
                                "dish_id": dish_id,
                                "recipe_id": recipe_id,
                                "recipe_source": RAW_SOURCE,
                                "raw_ingredient_text": parsed.raw_text,
                                "canonical_ingredient": ingredient,
                                "ingredient_norm_key": normalize_ingredient(ingredient) or ingredient,
                                "parsed_quantity": parsed.quantity,
                                "parsed_unit": parsed.unit,
                                "parsed_grams": grams,
                                "servings": None,
                                "grams_per_serving": None,
                                "fdc_id": conversion.get("fdc_id"),
                                "usda_description": None,
                                "parse_confidence": parsed.parse_confidence,
                                "match_confidence": 1.0 if conversion.get("fdc_id") else None,
                            },
                        )
                        observations_inserted += 1
            if store_observations and observations_inserted != committed_observations:
                connection.commit()
                committed_observations = observations_inserted

    inserted_or_updated = 0
    unmatched = 0
    dish_count = 0
    examples: dict[str, list[dict[str, Any]]] = {}

    for label in labels:
        recipe_count = int(recipe_counts[label])
        if recipe_count <= 0:
            continue
        candidates: list[dict[str, Any]] = []
        for ingredient, values in grams_by_label[label].items():
            observations = len(values)
            if observations < min_weight_observations:
                continue
            mean = _mean(values)
            mode = _mode(values)
            median = _median(values)
            grams_std = _std(values)
            grams_default, method = _portion_method(mean, mode, median, observations)
            match = nutrition_cache.get(ingredient)
            if match is None:
                match = _nutrition_match(ingredient, db_path)
                nutrition_cache[ingredient] = match
            if match.get("status") != "found":
                unmatched += 1
            pct_recipes_present = round(source_counts[label][ingredient] / max(1, recipe_count), 6)
            candidates.append(
                {
                    "dish_id": ids_by_label[label],
                    "canonical_ingredient": ingredient,
                    "ingredient_norm_key": normalize_ingredient(ingredient) or ingredient,
                    "fdc_id": match.get("fdc_id"),
                    "usda_description": match.get("matched_ingredient"),
                    "prep_state": None,
                    "grams_mean": mean,
                    "grams_mode": mode,
                    "grams_median": median,
                    "grams_std": grams_std,
                    "grams_default": grams_default,
                    "grams_per_100g_dish": None,
                    "portion_method": method,
                    "pct_recipes_present": pct_recipes_present,
                    "ingredient_rank": 0,
                    "n_recipes": recipe_count,
                    "n_weight_observations": observations,
                    "default_include": 1 if grams_default is not None and match.get("status") == "found" else 0,
                    "confidence": pct_recipes_present,
                    "source": RAW_SOURCE,
                }
            )

        rows = sorted(
            _collapse_rows_by_nutrition_source(candidates),
            key=lambda item: (
                float(item.get("pct_recipes_present") or 0.0),
                int(item.get("n_weight_observations") or 0),
                float(item.get("grams_default") or 0.0),
            ),
            reverse=True,
        )[:top_ingredients_per_dish]
        if not rows:
            continue
        _set_grams_per_100g_dish(rows)
        dish_grams_mean = sum(float(row.get("grams_mean") or 0.0) for row in rows)
        dish_count += 1
        for index, row in enumerate(rows, start=1):
            row["ingredient_rank"] = index
            _upsert_prior(connection, row)
            inserted_or_updated += 1

        totals = recipe_total_grams[label]
        connection.execute(
            """
            INSERT INTO dish_portion_priors (
                dish_id, dish_grams_mean, serving_grams_median, serving_grams_mode,
                n_recipes, confidence, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(dish_id) DO UPDATE SET
                dish_grams_mean = excluded.dish_grams_mean,
                serving_grams_median = excluded.serving_grams_median,
                serving_grams_mode = excluded.serving_grams_mode,
                n_recipes = excluded.n_recipes,
                confidence = excluded.confidence,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                ids_by_label[label],
                dish_grams_mean if dish_grams_mean > 0.0 else None,
                _median(totals),
                _mode(totals),
                int(converted_recipe_counts[label]) or None,
                round(int(converted_recipe_counts[label]) / max(1, recipe_count), 6),
                RAW_SOURCE,
            ),
        )
        if label in {"lasagna", "spaghetti_bolognese", "waffles", "donuts"}:
            examples[label] = [
                {
                    "ingredient": row["canonical_ingredient"],
                    "grams_mean": row["grams_mean"],
                    "grams_default": row["grams_default"],
                    "grams_per_100g_dish": row["grams_per_100g_dish"],
                    "portion_method": row["portion_method"],
                    "fdc_id": row["fdc_id"],
                    "default_include": row["default_include"],
                }
                for row in rows[:8]
            ]

    return {
        "source": RAW_SOURCE,
        "recipe_rows_scanned": scanned_rows,
        "matched_recipe_rows": matched_recipe_rows,
        "observations_inserted": observations_inserted,
        "dishes_with_priors": dish_count,
        "dish_ingredient_priors_created_or_updated": inserted_or_updated,
        "unmatched_ingredients": unmatched,
        "examples": examples,
    }


def build_db_dish_ingredient_priors(
    db_path: str | Path = DB_PATH,
    dish_mass_priors_path: str | Path = DISH_MASS_PRIORS_PATH,
    dish_priors_path: str | Path = DISH_PRIORS_PATH,
    top_ingredients_per_dish: int = 25,
    use_recipe_nlg_raw: bool = False,
    recipe_nlg_path: str | Path | None = None,
    min_weight_observations: int = 3,
    max_rows: int | None = None,
    dishes: list[str] | None = None,
    store_observations: bool = True,
) -> dict[str, Any]:
    database_path = Path(db_path)
    run_migration(database_path, create_backup=True)
    mass_payload = _load_json(Path(dish_mass_priors_path))
    frequency_payload = _load_json(Path(dish_priors_path))
    mass_priors = mass_payload.get("dish_mass_priors") or {}
    frequency_priors = frequency_payload.get("dish_priors") or {}

    inserted_or_updated = 0
    unmatched = 0
    dish_count = 0
    examples: dict[str, list[dict[str, Any]]] = {}

    with closing(sqlite3.connect(database_path)) as connection:
        ids_by_label = _dish_ids(connection)
        _clear_derived_rows(connection)
        connection.commit()
        if use_recipe_nlg_raw:
            raw_summary = _build_db_priors_from_raw_recipes(
                connection=connection,
                db_path=database_path,
                recipe_nlg_path=resolve_recipe_nlg_path(recipe_nlg_path),
                top_ingredients_per_dish=top_ingredients_per_dish,
                min_weight_observations=min_weight_observations,
                max_rows=max_rows,
                dish_filter={dish.strip().lower().replace(" ", "_") for dish in dishes} if dishes else None,
                store_observations=store_observations,
            )
            connection.commit()
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("dishes", "dish_ingredient_priors", "dish_portion_priors", "recipe_ingredient_observations")
            }
            raw_summary.update({"db_path": str(database_path), "table_counts": counts})
            return raw_summary

        labels = sorted(set(ids_by_label) & (set(mass_priors) | set(frequency_priors)))
        for label in labels:
            dish_id = ids_by_label[label]
            mass_prior = mass_priors.get(label) or {}
            frequency_rows = frequency_priors.get(label) or []
            recipe_count = int(mass_prior.get("recipe_count") or 0)
            converted_recipe_count = int(mass_prior.get("converted_recipe_count") or 0)
            rows: list[dict[str, Any]] = []
            seen_ingredients: set[str] = set()
            mass_by_ingredient: dict[str, dict[str, Any]] = {}

            for item in (mass_prior.get("ingredients") or [])[:top_ingredients_per_dish]:
                ingredient = normalize_ingredient(str(item.get("ingredient") or "")) or str(item.get("ingredient") or "")
                if not ingredient:
                    continue
                mass_by_ingredient.setdefault(ingredient, item)

            for item in frequency_rows[:top_ingredients_per_dish]:
                ingredient = normalize_ingredient(str(item.get("ingredient") or "")) or str(item.get("ingredient") or "")
                if not ingredient or ingredient in seen_ingredients:
                    continue
                seen_ingredients.add(ingredient)
                mass_item = mass_by_ingredient.get(ingredient)
                source = PRIOR_SOURCE if mass_item else FREQUENCY_SOURCE
                if mass_item:
                    median = _safe_float(mass_item.get("median_grams"))
                    mean = _safe_float(mass_item.get("mean_grams"))
                    mode = _safe_float(mass_item.get("mode_grams"))
                    grams_std = _safe_float(mass_item.get("std_grams"))
                    observations = int(mass_item.get("source_count") or item.get("source_count") or 0)
                    mass_fraction = _safe_float(mass_item.get("mass_fraction"))
                    grams_default = round(mass_fraction * 100.0, 6) if mass_fraction is not None else None
                    method = "mass_fraction_100g" if grams_default is not None else _portion_method(mean, mode, median, observations)[1]
                    grams_default, method = _cap_grams_per_100g(ingredient, grams_default, method)
                    pct_recipes_present = round(observations / max(1, recipe_count), 6) if recipe_count else float(item.get("weight") or 0.0)
                else:
                    median = None
                    mean = None
                    mode = None
                    grams_std = None
                    observations = int(item.get("source_count") or 0)
                    grams_default = round(float(item.get("weight") or 0.0) * 100.0, 6)
                    method = "frequency_100g_fallback" if grams_default > 0.0 else "missing"
                    grams_default, method = _cap_grams_per_100g(ingredient, grams_default, method)
                    pct_recipes_present = float(item.get("weight") or 0.0)
                match = _nutrition_match(ingredient, database_path)
                if match.get("status") != "found":
                    unmatched += 1
                rows.append(
                    {
                        "dish_id": dish_id,
                        "canonical_ingredient": ingredient,
                        "ingredient_norm_key": normalize_ingredient(ingredient) or ingredient,
                        "fdc_id": match.get("fdc_id"),
                        "usda_description": match.get("matched_ingredient"),
                        "prep_state": None,
                        "grams_mean": mean,
                        "grams_mode": mode,
                        "grams_median": median,
                        "grams_std": grams_std,
                        "grams_default": grams_default,
                        "grams_per_100g_dish": grams_default,
                        "portion_method": method,
                        "pct_recipes_present": pct_recipes_present,
                        "ingredient_rank": len(rows) + 1,
                        "n_recipes": recipe_count or None,
                        "n_weight_observations": observations or None,
                        "default_include": 1 if grams_default is not None and match.get("status") == "found" else 0,
                        "confidence": float(item.get("weight") or 0.0),
                        "source": source,
                    }
                )

            if not rows:
                for item in (mass_prior.get("ingredients") or [])[:top_ingredients_per_dish]:
                    ingredient = normalize_ingredient(str(item.get("ingredient") or "")) or str(item.get("ingredient") or "")
                    if not ingredient or ingredient in seen_ingredients:
                        continue
                    seen_ingredients.add(ingredient)
                    median = _safe_float(item.get("median_grams"))
                    mean = _safe_float(item.get("mean_grams"))
                    mode = _safe_float(item.get("mode_grams"))
                    observations = int(item.get("source_count") or 0)
                    mass_fraction = _safe_float(item.get("mass_fraction"))
                    grams_default = round(mass_fraction * 100.0, 6) if mass_fraction is not None else None
                    method = "mass_fraction_100g" if grams_default is not None else _portion_method(mean, mode, median, observations)[1]
                    grams_default, method = _cap_grams_per_100g(ingredient, grams_default, method)
                    match = _nutrition_match(ingredient, database_path)
                    if match.get("status") != "found":
                        unmatched += 1
                    rows.append(
                        {
                            "dish_id": dish_id,
                            "canonical_ingredient": ingredient,
                            "ingredient_norm_key": normalize_ingredient(ingredient) or ingredient,
                            "fdc_id": match.get("fdc_id"),
                            "usda_description": match.get("matched_ingredient"),
                            "prep_state": None,
                            "grams_mean": mean,
                            "grams_mode": mode,
                            "grams_median": median,
                            "grams_std": _safe_float(item.get("std_grams")),
                            "grams_default": grams_default,
                            "grams_per_100g_dish": grams_default,
                            "portion_method": method,
                            "pct_recipes_present": round(observations / max(1, recipe_count), 6) if recipe_count else None,
                            "ingredient_rank": len(rows) + 1,
                            "n_recipes": recipe_count or None,
                            "n_weight_observations": observations or None,
                            "default_include": 1 if grams_default is not None and match.get("status") == "found" else 0,
                            "confidence": round(min(1.0, (observations / max(1, recipe_count)) if recipe_count else 0.0), 6),
                            "source": PRIOR_SOURCE,
                        }
                    )

            if rows:
                rows = _collapse_rows_by_nutrition_source(rows)
                _set_grams_per_100g_dish(rows)
                for index, row in enumerate(rows, start=1):
                    row["ingredient_rank"] = index
                dish_count += 1
                dish_grams_mean = sum(float(row.get("grams_mean") or row.get("grams_default") or 0.0) for row in rows)
                connection.execute(
                    """
                    INSERT INTO dish_portion_priors (
                        dish_id, dish_grams_mean, serving_grams_median, n_recipes, confidence, source, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(dish_id) DO UPDATE SET
                        dish_grams_mean = excluded.dish_grams_mean,
                        serving_grams_median = excluded.serving_grams_median,
                        n_recipes = excluded.n_recipes,
                        confidence = excluded.confidence,
                        source = excluded.source,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        dish_id,
                        dish_grams_mean if dish_grams_mean > 0.0 else None,
                        dish_grams_mean if dish_grams_mean > 0.0 else None,
                        converted_recipe_count or recipe_count or None,
                        float(mass_prior.get("coverage") or 0.0),
                        PRIOR_SOURCE if dish_grams_mean > 0.0 else FREQUENCY_SOURCE,
                    ),
                )
            for row in rows:
                _upsert_prior(connection, row)
                inserted_or_updated += 1
            if label in {"lasagna", "spaghetti_bolognese", "waffles", "donuts"}:
                examples[label] = [
                    {
                        "ingredient": row["canonical_ingredient"],
                        "grams_default": row["grams_default"],
                        "grams_per_100g_dish": row["grams_per_100g_dish"],
                        "portion_method": row["portion_method"],
                        "fdc_id": row["fdc_id"],
                        "default_include": row["default_include"],
                    }
                    for row in rows[:8]
                ]
        connection.commit()

        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("dishes", "dish_ingredient_priors", "dish_portion_priors", "recipe_ingredient_observations")
        }

    return {
        "db_path": str(database_path),
        "source": PRIOR_SOURCE,
        "dishes_with_priors": dish_count,
        "dish_ingredient_priors_created_or_updated": inserted_or_updated,
        "unmatched_ingredients": unmatched,
        "table_counts": counts,
        "examples": examples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Populate DB-backed dish ingredient priors from PALM RecipeNLG evidence.")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--dish-mass-priors-path", type=Path, default=DISH_MASS_PRIORS_PATH)
    parser.add_argument("--dish-priors-path", type=Path, default=DISH_PRIORS_PATH)
    parser.add_argument("--recipe-nlg", type=Path, default=None)
    parser.add_argument("--top-ingredients-per-dish", type=int, default=25)
    parser.add_argument("--use-recipe-nlg-raw", action="store_true", help="Scan RecipeNLG ingredient lines and calculate grams_mean/mode/median/std from raw observations.")
    parser.add_argument("--min-weight-observations", type=int, default=3)
    parser.add_argument("--max-rows", type=int, default=None, help="Optional smoke-test limit; omit for full offline raw build.")
    parser.add_argument("--dish", action="append", help="Optional Food-101 label to build; can be repeated.")
    parser.add_argument("--skip-observations", action="store_true", help="Do not write recipe_ingredient_observations rows during raw builds.")
    args = parser.parse_args()

    summary = build_db_dish_ingredient_priors(
        db_path=args.db_path,
        dish_mass_priors_path=args.dish_mass_priors_path,
        dish_priors_path=args.dish_priors_path,
        top_ingredients_per_dish=args.top_ingredients_per_dish,
        use_recipe_nlg_raw=args.use_recipe_nlg_raw,
        recipe_nlg_path=args.recipe_nlg,
        min_weight_observations=args.min_weight_observations,
        max_rows=args.max_rows,
        dishes=args.dish,
        store_observations=not args.skip_observations,
    )
    print("PALM DB Dish Ingredient Prior Build")
    print("=" * 39)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
