"""Build the compact PALM V2 runtime database from the required USDA CSVs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from food_ai.config import PALM_V2_DB_PATH, USDA_DATA_DIR
from food_ai.food_bridge.build_dish_priors import _label_matchers, _load_class_labels, _title_matches_dish
from food_ai.food_bridge.config import DISH_MASS_PRIORS_PATH, DISH_PRIORS_PATH
from food_ai.food_bridge.ingredient_normalize import CANONICAL_INGREDIENT_ALIASES, ingredient_aliases_for, normalize_ingredient
from food_ai.utils.usda_normalize import normalize_usda_ingredient


RAW_SOURCE_FILES: tuple[tuple[str, str], ...] = (
    ("food.csv", "usda_food"),
    ("food_category.csv", "usda_food_category"),
    ("food_nutrient.csv", "usda_food_nutrient"),
    ("nutrient.csv", "usda_nutrient"),
    ("food_nutrient_derivation.csv", "usda_food_nutrient_derivation"),
    ("food_nutrient_source.csv", "usda_food_nutrient_source"),
    ("food_portion.csv", "usda_food_portion"),
    ("measure_unit.csv", "usda_measure_unit"),
    ("foundation_food.csv", "usda_foundation_food"),
    ("sr_legacy_food.csv", "usda_sr_legacy_food"),
    ("survey_fndds_food.csv", "usda_survey_fndds_food"),
    ("input_food.csv", "usda_input_food"),
    ("fndds_ingredient_nutrient_value.csv", "usda_fndds_ingredient_nutrient_value"),
    ("fndds_derivation.csv", "usda_fndds_derivation"),
    ("wweia_food_category.csv", "usda_wweia_food_category"),
    ("branded_food.csv", "usda_branded_food"),
)

DOWNSTREAM_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "food.csv": ("fdc_id", "data_type", "description", "food_category_id", "publication_date"),
    "food_category.csv": ("id", "description"),
    "food_nutrient.csv": ("fdc_id", "nutrient_id", "amount", "derivation_id"),
    "nutrient.csv": ("id", "name", "unit_name", "nutrient_nbr"),
    "food_nutrient_derivation.csv": ("id", "code", "description"),
    "food_portion.csv": ("fdc_id", "amount", "measure_unit_id", "portion_description", "modifier", "gram_weight", "data_points"),
    "measure_unit.csv": ("id", "name"),
    "foundation_food.csv": ("fdc_id",),
    "sr_legacy_food.csv": ("fdc_id",),
    "survey_fndds_food.csv": ("fdc_id",),
    "input_food.csv": ("fdc_id", "fdc_id_of_input_food", "sr_description", "gram_weight"),
    "fndds_ingredient_nutrient_value.csv": ("Ingredient description", "Nutrient code", "Nutrient value", "FDC ID"),
    "fndds_derivation.csv": ("derivation code", "derivation description"),
    "wweia_food_category.csv": ("wweia_food_category", "wweia_food_category_description"),
    "branded_food.csv": ("fdc_id", "gtin_upc", "serving_size", "serving_size_unit", "household_serving_fulltext", "brand_owner", "brand_name"),
}

TARGET_NUTRIENTS: dict[str, dict[str, str]] = {
    "calories_per_100g": {"nutrient_nbr": "208", "unit_name": "KCAL", "preferred_name": "Energy"},
    "protein_g_per_100g": {"nutrient_nbr": "203", "unit_name": "G", "preferred_name": "Protein"},
    "fat_g_per_100g": {"nutrient_nbr": "204", "unit_name": "G", "preferred_name": "Total lipid (fat)"},
    "saturated_fat_g_per_100g": {"nutrient_nbr": "606", "unit_name": "G", "preferred_name": "Fatty acids, total saturated"},
    "carbs_g_per_100g": {"nutrient_nbr": "205", "unit_name": "G", "preferred_name": "Carbohydrate, by difference"},
    "fiber_g_per_100g": {"nutrient_nbr": "291", "unit_name": "G", "preferred_name": "Fiber, total dietary"},
    "sugar_g_per_100g": {"nutrient_nbr": "269", "unit_name": "G", "preferred_name": "Total Sugars"},
    "sodium_mg_per_100g": {"nutrient_nbr": "307", "unit_name": "MG", "preferred_name": "Sodium, Na"},
    "cholesterol_mg_per_100g": {"nutrient_nbr": "601", "unit_name": "MG", "preferred_name": "Cholesterol"},
    "calcium_mg_per_100g": {"nutrient_nbr": "301", "unit_name": "MG", "preferred_name": "Calcium, Ca"},
    "iron_mg_per_100g": {"nutrient_nbr": "303", "unit_name": "MG", "preferred_name": "Iron, Fe"},
    "potassium_mg_per_100g": {"nutrient_nbr": "306", "unit_name": "MG", "preferred_name": "Potassium, K"},
}

CORE_NUTRIENT_COLUMNS: tuple[str, ...] = (
    "calories_per_100g",
    "protein_g_per_100g",
    "fat_g_per_100g",
    "carbs_g_per_100g",
)

DIRECT_SOURCE_PRIORITY: tuple[str, ...] = (
    "foundation_food",
    "sr_legacy_food",
    "survey_fndds_food",
)

FOUNDATION_PRIORITY_RULE = "usda_foundation_food_membership"

EXPLICIT_SAMPLE_ALIASES: tuple[str, ...] = (
    "cooked spaghetti",
    "dry spaghetti",
    "cooked pasta",
    "dry pasta",
    "lasagna noodle",
    "lasagna noodles",
    "ricotta cheese",
    "mozzarella cheese",
    "tomato sauce",
    "ground beef",
    "glazed donut",
    "glazed doughnut",
    "lasagna",
)

EXPLICIT_ALIAS_PREFERENCES: dict[str, tuple[dict[str, tuple[str, ...]], ...]] = {
    "egg": (
        {"source_names": ("sr_legacy_food", "survey_fndds_food"), "description_substrings": ("egg, whole, raw, fresh", "egg, whole, raw")},
    ),
    "milk": (
        {"source_names": ("foundation_food", "sr_legacy_food"), "description_substrings": ("milk, whole, 3.25% milkfat, with added vitamin d",)},
        {"source_names": ("sr_legacy_food",), "description_substrings": ("milk, low fat (1%)",)},
    ),
    "buttermilk": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("milk, buttermilk, fluid, cultured", "buttermilk")},
    ),
    "flour": (
        {"source_names": ("foundation_food", "sr_legacy_food"), "description_substrings": ("flour, wheat, all-purpose",)},
    ),
    "all purpose": (
        {"source_names": ("foundation_food", "sr_legacy_food"), "description_substrings": ("flour, wheat, all-purpose",)},
    ),
    "wheat flour": (
        {"source_names": ("foundation_food", "sr_legacy_food"), "description_substrings": ("flour, wheat, all-purpose",)},
    ),
    "sugar": (
        {"source_names": ("foundation_food", "sr_legacy_food"), "description_substrings": ("sugars, granulated",)},
    ),
    "granulated sugar": (
        {"source_names": ("foundation_food", "sr_legacy_food"), "description_substrings": ("sugars, granulated",)},
    ),
    "brown sugar": (
        {"source_names": ("foundation_food", "sr_legacy_food"), "description_substrings": ("sugars, brown", "brown sugar")},
    ),
    "glaze": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("frostings, glaze, prepared-from-recipe",)},
        {"source_names": ("survey_fndds_food",), "description_substrings": ("icing, white",)},
    ),
    "icing": (
        {"source_names": ("survey_fndds_food",), "description_substrings": ("icing, white", "icing, chocolate")},
        {"source_names": ("sr_legacy_food",), "description_substrings": ("frostings, glaze, prepared-from-recipe",)},
    ),
    "oil": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("oil, vegetable, soybean, refined", "oil, vegetable")},
    ),
    "olive oil": (
        {"source_names": ("survey_fndds_food", "sr_legacy_food"), "description_substrings": ("olive oil", "oil, olive")},
    ),
    "butter": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("butter, salted", "butter, without salt")},
    ),
    "shortening": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("shortening, vegetable, household, composite",)},
    ),
    "onion": (
        {"source_names": ("sr_legacy_food", "survey_fndds_food"), "description_substrings": ("onions, raw", "onions, cooked")},
    ),
    "green pepper": (
        {"source_names": ("sr_legacy_food", "survey_fndds_food"), "description_substrings": ("peppers, sweet, green, raw", "peppers, sweet, green, cooked", "peppers, sweet, green, sauteed")},
    ),
    "mushroom": (
        {"source_names": ("survey_fndds_food", "sr_legacy_food"), "description_substrings": ("mushrooms, raw", "mushrooms, cooked")},
    ),
    "bread": (
        {"source_names": ("sr_legacy_food", "survey_fndds_food"), "description_substrings": ("bread, white", "bread, whole-wheat", "bread, wheat")},
    ),
    "bun": (
        {"source_names": ("survey_fndds_food", "sr_legacy_food"), "description_substrings": ("rolls, hamburger", "hamburger bun", "bun, hamburger")},
    ),
    "cooked pasta": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("pasta, cooked, enriched, without added salt",)},
        {"source_names": ("survey_fndds_food",), "description_substrings": ("pasta, cooked",)},
    ),
    "cooked spaghetti": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("pasta, cooked, enriched, without added salt",)},
        {"source_names": ("survey_fndds_food",), "description_substrings": ("pasta, cooked",)},
    ),
    "dry pasta": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("pasta, dry, enriched",)},
    ),
    "dry spaghetti": (
        {"source_names": ("foundation_food",), "description_substrings": ("pasta, dry, enriched, spaghetti",)},
        {"source_names": ("sr_legacy_food",), "description_substrings": ("pasta, dry, enriched",)},
    ),
    "lasagna": (
        {"source_names": ("survey_fndds_food",), "description_substrings": ("lasagna with meat",)},
        {"source_names": ("sr_legacy_food",), "description_substrings": ("lasagna with meat & sauce, frozen entree",)},
    ),
    "lasagna noodle": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("pasta mix, italian lasagna, unprepared",)},
    ),
    "lasagna noodles": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("pasta mix, italian lasagna, unprepared",)},
    ),
    "milk": (
        {"source_names": ("foundation_food", "sr_legacy_food"), "description_substrings": ("milk, whole, 3.25% milkfat, with added vitamin d",)},
    ),
    "whole milk": (
        {"source_names": ("foundation_food", "sr_legacy_food"), "description_substrings": ("milk, whole, 3.25% milkfat, with added vitamin d",)},
    ),
    "ricotta": (
        {"source_names": ("foundation_food",), "description_substrings": ("cheese, ricotta, whole milk",)},
    ),
    "ricotta cheese": (
        {"source_names": ("foundation_food",), "description_substrings": ("cheese, ricotta, whole milk",)},
    ),
    "mozzarella": (
        {"source_names": ("foundation_food",), "description_substrings": ("cheese, mozzarella, low moisture, part-skim",)},
    ),
    "mozzarella cheese": (
        {"source_names": ("foundation_food",), "description_substrings": ("cheese, mozzarella, low moisture, part-skim",)},
    ),
    "tomato sauce": (
        {"source_names": ("foundation_food",), "description_substrings": ("sauce, pasta, spaghetti/marinara, ready-to-serve",)},
    ),
    "extra virgin olive oil": (
        {"source_names": ("survey_fndds_food",), "description_substrings": ("olive oil",)},
    ),
    "butter stick salted": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("butter, salted",)},
    ),
    "butter stick unsalted": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("butter, without salt",)},
    ),
    "ground beef": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("beef, ground, 95% lean meat / 5% fat, raw",)},
        {"source_names": ("sr_legacy_food",), "description_substrings": ("beef, ground, 90% lean meat / 10% fat, raw",)},
    ),
    "glazed donut": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("doughnuts, yeast-leavened, glazed, enriched",)},
        {"source_names": ("sr_legacy_food",), "description_substrings": ("doughnuts, cake-type, plain, sugared or glazed",)},
    ),
    "glazed doughnut": (
        {"source_names": ("sr_legacy_food",), "description_substrings": ("doughnuts, yeast-leavened, glazed, enriched",)},
        {"source_names": ("sr_legacy_food",), "description_substrings": ("doughnuts, cake-type, plain, sugared or glazed",)},
    ),
}

PREP_STATE_PRIORITY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("unprepared", (" unprepared", ", unprepared", "uncooked", "not prepared")),
    ("dry", (" dry", ", dry", "dehydrated", "powder", "mix, dry", "dried")),
    ("raw", (" raw", ", raw", " raw,", "without cooking", "as purchased")),
    ("cooked", (" cooked", "boiled", "broiled", "baked", "roasted", "grilled", "sauteed", "pan-browned", "fried")),
    ("prepared", (" prepared", ", prepared")),
)

COMPOSITE_EVIDENCE_PATTERNS: tuple[str, ...] = (
    "restaurant",
    "entree",
    "frozen entree",
    "frozen meal",
    "prepared meal",
    " dinner",
    " meal",
    " dish",
    "platter",
    "with sauce",
    "with meat sauce",
    "ravioli",
    "lasagna with",
    "pizza",
    "casserole",
    "burrito",
    "sandwich",
    "breakfast bowl",
    "commercial",
    "fast food",
)

PACKAGED_PRODUCT_PATTERNS: tuple[str, ...] = (
    "mix",
    "prepared-from-recipe",
    "refrigerated dough",
    "ready-to-bake",
    "ready-to-serve",
    "packaged",
    "commercial",
    "dry mix",
)

PARENT_VARIANT_SKIP_PATTERNS: tuple[str, ...] = (
    "dessert",
    "breakfast",
    "pizza rolls",
    "pizza pocket",
    "turnover or hot pocket",
    "topping from",
    "rice paper",
    "not fried",
    "school lunch",
    "from frozen",
    "from restaurant or fast food",
    "stuffed crust",
    "with fruit",
    "mexican pizza",
)

USER_FACING_INGREDIENT_ALLOWLIST: tuple[str, ...] = (
    "tomato sauce",
    "pasta sauce",
    "meat sauce",
    "cheese sauce",
    "lasagna noodles",
    "lasagna noodle",
    "pasta",
    "noodles",
    "noodle",
    "ground beef",
    "ricotta cheese",
    "mozzarella cheese",
    "parmesan cheese",
)

AMBIGUOUS_CHILD_ALIASES: set[str] = {
    "ground",
    "turkey ground",
    "chicken ground",
    "beef",
    "turkey",
    "chicken",
}

EXPLICIT_DRY_FORM_TOKENS: tuple[str, ...] = (
    "dried",
    "dry",
    "powder",
    "powdered",
    "dehydrated",
    "instant",
    "mix",
    "unprepared",
)

INGREDIENT_ROLE_PATTERNS: dict[str, tuple[str, ...]] = {
    "starch/base": ("pasta", "noodle", "rice", "bread", "bun", "roll", "flour", "crust", "dough", "tortilla", "macaroni"),
    "protein": ("beef", "chicken", "turkey", "pork", "ham", "sausage", "pepperoni", "shrimp", "tofu", "bean", "egg", "meat", "tuna"),
    "dairy": ("cheese", "mozzarella", "ricotta", "parmesan", "cheddar", "milk", "cream", "yogurt", "buttermilk", "butter"),
    "fat/oil": ("oil", "olive oil", "butter", "margarine", "shortening"),
    "sauce": ("sauce", "salsa", "catsup", "ketchup", "mustard", "marinara", "glaze", "icing", "frosting", "gravy"),
    "sweetener": ("sugar", "honey", "syrup"),
    "vegetable": ("onion", "garlic", "pepper", "mushroom", "tomato", "spinach", "lettuce", "carrot", "pea", "broccoli", "scallion", "basil"),
    "fruit": ("strawberry", "blueberry", "apple", "peach", "peaches", "pineapple", "lime", "lemon", "cherry"),
    "spice/seasoning": ("salt", "pepper", "oregano", "basil", "coriander", "seasoning", "spice"),
    "leavening": ("yeast", "baking powder", "baking soda"),
    "liquid": ("water", "broth", "stock", "vinegar"),
    "garnish": ("parsley", "green onion"),
}

# Frequency/support weighting controls for candidate ranking.
FREQUENCY_PREVALENCE_MULTIPLIER = 0.55
FREQUENCY_SOURCECOUNT_MULTIPLIER = 0.25
RECIPE_SUPPORT_BLEND_WEIGHT = 0.70
FNDDS_PREVALENCE_BLEND_WEIGHT = 0.30

LEXICAL_STOPWORDS = {
    "raw", "cooked", "roasted", "boiled", "drained", "without", "with", "only", "lean", "fat",
    "fresh", "fluid", "prepared", "regular", "mixed", "species", "meat", "skin", "boneless",
    "domesticated", "whole", "home", "dry", "heat", "cultured", "plain", "enriched",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _q(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _normalize_spaces(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _schema_hash(header: list[str]) -> str:
    return hashlib.sha256(json.dumps(header, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _required_source_path(data_dir: Path, filename: str) -> Path:
    return data_dir / filename


def _as_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    if pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _match_tokens(text: str) -> set[str]:
    lowered = re.sub(r"[^a-z ]+", " ", str(text or "").lower())
    return {token for token in lowered.split() if token and token not in LEXICAL_STOPWORDS}


def _lexical_match_score(query: str, description: str) -> float:
    query_norm = str(query or "").lower().strip()
    normalized_candidate = normalize_usda_ingredient(description)
    candidate_source = normalized_candidate or str(description or "").lower().strip()

    query_tokens = _match_tokens(query_norm)
    candidate_tokens = _match_tokens(candidate_source)
    overlap = len(query_tokens & candidate_tokens)
    extras = len(candidate_tokens - query_tokens)
    missing = len(query_tokens - candidate_tokens)
    score = 2.4 * overlap - 0.45 * extras - 0.35 * missing

    animals = {"beef", "pork", "chicken", "duck", "turkey", "quail", "snapper", "fish"}
    query_animals = query_tokens & animals
    candidate_animals = candidate_tokens & animals
    if query_animals and candidate_animals and query_animals != candidate_animals:
        score -= 3.5
    elif query_animals and not candidate_animals:
        score -= 2.0

    specificity = {
        "loin", "leg", "wing", "flank", "tenderloin", "ground", "liver", "giblet", "broth", "stock", "milk", "juice",
    }
    for token in specificity:
        if token in query_tokens and token not in candidate_tokens:
            score -= 1.7
    if "liver" in candidate_tokens and "liver" not in query_tokens:
        score -= 1.6
    if "giblet" in candidate_tokens and "giblet" not in query_tokens:
        score -= 1.6
    if "broth" in query_tokens and not ({"broth", "stock"} & candidate_tokens):
        score -= 2.2
    if "milk" in query_tokens and "milk" not in candidate_tokens:
        score -= 2.2
    return score


def _required_tokens(requested: str) -> set[str]:
    tokens = set(str(requested or "").lower().split())
    optional = {
        "beef", "pork", "chicken", "turkey", "duck", "fish", "snapper", "milk",
        "bread", "rice", "pasta", "mustard", "bagel", "cream", "bell", "pepper",
        "pea", "grapefruit", "lemon",
    }
    return {token for token in tokens if token not in optional}


def _has_required_tokens(description: str, requested: str) -> bool:
    required = _required_tokens(requested)
    if not required:
        return True
    lowered = str(description or "").lower()
    return all(token in lowered for token in required)


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 6) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def _mode(values: list[float]) -> float | None:
    if not values:
        return None
    rounded = [round(value, 3) for value in values]
    return float(Counter(rounded).most_common(1)[0][0])


def _extract_prep_state(description: str) -> str:
    lowered = f" {str(description or '').lower()} "
    for prep_state, phrases in PREP_STATE_PRIORITY:
        if any(phrase in lowered for phrase in phrases):
            return prep_state
    return "unspecified"


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_text = f" {_normalize_spaces(text)} "
    normalized_phrase = _normalize_spaces(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in normalized_text


def _is_user_facing_composite_text(text: str) -> bool:
    lowered = _normalize_spaces(text)
    if not lowered:
        return False
    if lowered in {_normalize_spaces(value) for value in USER_FACING_INGREDIENT_ALLOWLIST}:
        return False
    return any(_contains_phrase(lowered, phrase) for phrase in COMPOSITE_EVIDENCE_PATTERNS)


def _has_explicit_dry_form(text: str) -> bool:
    lowered = _normalize_spaces(text)
    return any(_contains_phrase(lowered, token) for token in EXPLICIT_DRY_FORM_TOKENS)


def _ingredient_role(text: str) -> str:
    lowered = _normalize_spaces(text)
    if not lowered:
        return "other"
    for role, patterns in INGREDIENT_ROLE_PATTERNS.items():
        if any(_contains_phrase(lowered, pattern) for pattern in patterns):
            return role
    return "other"


def _dish_family(label: str) -> str:
    lowered = _normalize_spaces(label.replace("_", " "))
    if "lasagna" in lowered:
        return "baked_pasta"
    if "spaghetti" in lowered:
        return "pasta_plate"
    if "pizza" in lowered:
        return "pizza"
    if "hamburger" in lowered or "burger" in lowered:
        return "burger"
    if "salad" in lowered:
        return "salad"
    if "fried rice" in lowered:
        return "fried_rice"
    if "pancake" in lowered:
        return "pancake"
    if "omelet" in lowered or "omelette" in lowered:
        return "omelet"
    if "macaroni and cheese" in lowered:
        return "mac_and_cheese"
    if "taco" in lowered or "burrito" in lowered:
        return "taco_burrito"
    if "curry" in lowered:
        return "curry"
    if "grilled cheese" in lowered or "sandwich" in lowered:
        return "sandwich"
    if "cake" in lowered:
        return "cake"
    if "donut" in lowered or "doughnut" in lowered:
        return "donut_pastry"
    return "other"


def _family_expected_roles(label: str) -> set[str]:
    family = _dish_family(label)
    expectations = {
        "baked_pasta": {"starch/base", "sauce", "dairy", "protein", "vegetable", "spice/seasoning"},
        "pasta_plate": {"starch/base", "sauce", "protein", "vegetable", "spice/seasoning"},
        "pizza": {"starch/base", "sauce", "dairy", "protein", "vegetable", "fat/oil", "spice/seasoning"},
        "burger": {"starch/base", "protein", "dairy", "sauce", "vegetable", "spice/seasoning"},
        "salad": {"vegetable", "sauce", "protein", "dairy", "fruit", "fat/oil"},
        "fried_rice": {"starch/base", "protein", "vegetable", "fat/oil", "sauce", "spice/seasoning"},
        "pancake": {"starch/base", "liquid", "protein", "fat/oil", "sweetener", "leavening"},
        "omelet": {"protein", "dairy", "vegetable", "fat/oil", "spice/seasoning"},
        "mac_and_cheese": {"starch/base", "dairy", "liquid", "fat/oil", "spice/seasoning"},
        "taco_burrito": {"starch/base", "protein", "dairy", "sauce", "vegetable", "spice/seasoning"},
        "curry": {"protein", "sauce", "vegetable", "spice/seasoning", "fat/oil", "liquid"},
        "sandwich": {"starch/base", "dairy", "protein", "fat/oil", "sauce", "vegetable"},
        "cake": {"starch/base", "sweetener", "protein", "fat/oil", "liquid", "leavening", "sauce"},
        "donut_pastry": {"starch/base", "sweetener", "fat/oil", "protein", "liquid", "leavening", "sauce"},
    }
    return expectations.get(family, set())


def _classify_ingredient_candidate(text: str, label: str, source_kind: str) -> tuple[str, str, str]:
    normalized = _normalize_spaces(text)
    if not normalized:
        return "reject", "empty_candidate", "other"
    role = _ingredient_role(normalized)
    label_phrase = _normalize_spaces(label.replace("_", " "))
    if source_kind == "parent_dish":
        return "dish_variant_evidence", "parent_dish_variant", role
    if _is_user_facing_composite_text(normalized):
        return "composite_dish", "composite_phrase", role
    if any(_contains_phrase(normalized, pattern) for pattern in PACKAGED_PRODUCT_PATTERNS):
        return "packaged_product", "packaged_or_mix_phrase", role
    if normalized == label_phrase or _contains_phrase(normalized, label_phrase):
        return "ambiguous", "echoes_dish_label", role
    if normalized in {"new", "silken", "plain", "regular"}:
        return "ambiguous", "underspecified_token", role
    return "true_ingredient", "accepted", role


def _score_candidate(
    *,
    label: str,
    source_kind: str,
    ingredient_role: str,
    prevalence: float,
    source_count: int,
    grams_per_100g: float,
    classification: str,
) -> float:
    base = {
        "fndds_input_food": 0.62,
        "recipenlg_frequency": 0.66,
        "recipenlg_mass": 0.52,
        "curated": 0.60,
    }.get(source_kind, 0.45)
    score = base
    score += min(0.30, max(0.0, prevalence) * FREQUENCY_PREVALENCE_MULTIPLIER)
    score += min(0.24, math.log1p(max(0, source_count)) * FREQUENCY_SOURCECOUNT_MULTIPLIER / 3.5)
    score += min(0.10, max(0.0, grams_per_100g) / 100.0 * 0.10)
    if ingredient_role in _family_expected_roles(label):
        score += 0.12
    elif ingredient_role == "other":
        score -= 0.08
    if classification != "true_ingredient":
        score -= 0.55
    return round(max(0.0, min(1.0, score)), 6)


def _should_use_parent_variant(label: str, description: str) -> tuple[bool, float, str]:
    lowered = _normalize_spaces(description)
    label_phrase = _normalize_spaces(label.replace("_", " "))
    label_tokens = [token for token in label_phrase.split() if token]
    if any(_contains_phrase(lowered, f"not {token}") for token in label_tokens):
        return False, 0.0, "negated_label_token"
    if len(label_tokens) > 1 and not all(re.search(rf"\b{re.escape(token)}\b", lowered) for token in label_tokens):
        return False, 0.0, "missing_label_anchor_tokens"
    if any(_contains_phrase(lowered, phrase) for phrase in PARENT_VARIANT_SKIP_PATTERNS):
        return False, 0.2, "parent_variant_conflict"
    family = _dish_family(label)
    if family == "pizza" and any(_contains_phrase(lowered, phrase) for phrase in ("dessert pizza", "pizza rolls", "topping from", "mexican pizza", "breakfast pizza")):
        return False, 0.15, "pizza_variant_conflict"
    if family == "fried_rice" and "fried rice" not in lowered:
        return False, 0.1, "fried_rice_phrase_missing"
    return True, 1.0, "parent_variant_used"


def _best_recipe_support(
    recipe_supports: dict[str, dict[str, Any]],
    canonical_name: str,
) -> dict[str, Any] | None:
    candidates = [
        _normalize_spaces(canonical_name),
        _normalize_spaces(normalize_ingredient(canonical_name) or canonical_name),
    ]
    for alias in ingredient_aliases_for(canonical_name):
        normalized = _normalize_spaces(normalize_ingredient(alias) or alias)
        if normalized:
            candidates.append(normalized)
    for candidate in candidates:
        support = recipe_supports.get(candidate)
        if support is not None:
            return support
    return None


def _normalized_child_alias_candidates(raw_description: str, recipe_identity_aliases: set[str]) -> list[str]:
    raw_text = str(raw_description or "").strip()
    normalized = normalize_ingredient(raw_text)
    candidates: list[str] = []

    heuristic_aliases: list[str] = []
    lowered = _normalize_spaces(raw_text)
    if lowered.endswith(" as ingredient"):
        heuristic_aliases.append(lowered.removesuffix(" as ingredient").strip())
    if "all purpose" in lowered:
        heuristic_aliases.insert(0, "flour")
    if "wheat flour" in lowered:
        heuristic_aliases.insert(0, "flour")
    if "granulated" in lowered and "sugar" in lowered:
        heuristic_aliases.insert(0, "sugar")
    if "brown sugar" in lowered:
        heuristic_aliases.insert(0, "brown sugar")
    if "olive oil" in lowered:
        heuristic_aliases.insert(0, "olive oil")
        heuristic_aliases.append("oil")
    elif re.search(r"\boil\b", lowered):
        heuristic_aliases.insert(0, "oil")
    if "buttermilk" in lowered:
        heuristic_aliases.insert(0, "buttermilk")
        heuristic_aliases.append("milk")
    elif "milk" in lowered and not _has_explicit_dry_form(lowered):
        heuristic_aliases.insert(0, "milk")
    if "egg" in lowered and not _has_explicit_dry_form(lowered):
        heuristic_aliases.insert(0, "egg")
    if "glaze" in lowered or "icing" in lowered:
        heuristic_aliases.insert(0, "glaze")
    if "green pepper" in lowered or ("pepper" in lowered and "green" in lowered):
        heuristic_aliases.insert(0, "green pepper")
    if "onion" in lowered:
        heuristic_aliases.insert(0, "onion")
    if "mushroom" in lowered:
        heuristic_aliases.insert(0, "mushroom")
    if "hamburger" in lowered and ("roll" in lowered or "bun" in lowered):
        heuristic_aliases.insert(0, "bun")
        heuristic_aliases.append("bread")
    if "hot dog" in lowered and "roll" in lowered:
        heuristic_aliases.insert(0, "bun")
    if "spaghetti sauce" in lowered or "marinara" in lowered or "pasta sauce" in lowered:
        heuristic_aliases.insert(0, "tomato sauce")
    if "ricotta" in lowered:
        heuristic_aliases.insert(0, "ricotta")
    if "mozzarella" in lowered:
        heuristic_aliases.insert(0, "mozzarella")
    if "parmesan" in lowered:
        heuristic_aliases.insert(0, "parmesan")
    if "cheddar" in lowered:
        heuristic_aliases.insert(0, "cheddar")
    if "ground" in lowered:
        if "beef" in lowered:
            heuristic_aliases.append("ground beef")
        elif "turkey" in lowered:
            heuristic_aliases.append("turkey")
        elif "chicken" in lowered:
            heuristic_aliases.append("chicken")
        elif "meat" in lowered:
            heuristic_aliases.append("meat")

    explicit_usda_alias = normalize_usda_ingredient(raw_text)
    if explicit_usda_alias:
        heuristic_aliases.append(explicit_usda_alias)

    if recipe_identity_aliases:
        raw_tokens = _match_tokens(raw_text)
        best_recipe_alias: str | None = None
        best_recipe_score: tuple[int, int, int] | None = None
        for alias in recipe_identity_aliases:
            alias_tokens = _match_tokens(alias)
            overlap = len(raw_tokens & alias_tokens)
            if overlap <= 0:
                continue
            score = (overlap, len(alias_tokens), -len(alias))
            if best_recipe_score is None or score > best_recipe_score:
                best_recipe_alias = alias
                best_recipe_score = score
        if best_recipe_alias is not None and normalized in AMBIGUOUS_CHILD_ALIASES:
            heuristic_aliases.insert(0, best_recipe_alias)

    for candidate in heuristic_aliases:
        key = _normalize_spaces(candidate)
        if key and key not in candidates:
            candidates.append(key)
    normalized_key = _normalize_spaces(normalized)
    if normalized_key:
        candidates.append(normalized_key)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def _load_recipe_identity_aliases_by_label(
    fallback_mass_priors: dict[str, Any],
    fallback_frequency_priors: dict[str, Any],
) -> dict[str, set[str]]:
    aliases_by_label: dict[str, set[str]] = defaultdict(set)
    for label, payload in fallback_mass_priors.items():
        for item in payload.get("ingredients") or []:
            normalized = normalize_ingredient(str(item.get("ingredient") or ""))
            if normalized:
                aliases_by_label[str(label)].add(normalized)
    for label, items in fallback_frequency_priors.items():
        for item in items or []:
            normalized = normalize_ingredient(str(item.get("ingredient") or ""))
            if normalized:
                aliases_by_label[str(label)].add(normalized)
    return aliases_by_label


def _recipe_label_denominator(
    mass_payload: dict[str, Any],
    frequency_items: list[dict[str, Any]],
) -> int:
    denominator = int(
        mass_payload.get("converted_recipe_count")
        or mass_payload.get("recipe_count")
        or 0
    )
    if denominator > 0:
        return denominator
    if not frequency_items:
        return 0
    return max(int(item.get("source_count") or 0) for item in frequency_items)


def _load_recipe_candidate_supports(
    fallback_mass_priors: dict[str, Any],
    fallback_frequency_priors: dict[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    supports_by_label: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    labels = set(fallback_mass_priors.keys()) | set(fallback_frequency_priors.keys())
    for label in labels:
        mass_payload = fallback_mass_priors.get(label) or {}
        frequency_items = list(fallback_frequency_priors.get(label) or [])
        denominator = max(1, _recipe_label_denominator(mass_payload, frequency_items))
        for source_kind, items in (
            ("recipenlg_mass", list(mass_payload.get("ingredients") or [])),
            ("recipenlg_frequency", frequency_items),
        ):
            for item in items:
                ingredient_text = str(item.get("ingredient") or "")
                normalized = normalize_ingredient(ingredient_text)
                if not normalized:
                    continue
                classification, reason, ingredient_role = _classify_ingredient_candidate(
                    ingredient_text,
                    label,
                    source_kind,
                )
                bucket = supports_by_label[str(label)].setdefault(
                    _normalize_spaces(normalized),
                    {
                        "normalized": _normalize_spaces(normalized),
                        "ingredient_texts": set(),
                        "source_count": 0,
                        "prevalence": 0.0,
                        "mass_fraction": 0.0,
                        "frequency_weight": 0.0,
                        "classification": classification,
                        "reason": reason,
                        "ingredient_role": ingredient_role,
                        "sources": set(),
                    },
                )
                bucket["ingredient_texts"].add(ingredient_text)
                bucket["source_count"] = max(bucket["source_count"], int(item.get("source_count") or 0))
                bucket["prevalence"] = max(
                    float(bucket.get("prevalence") or 0.0),
                    float(item.get("source_count") or 0) / denominator,
                )
                bucket["mass_fraction"] = max(float(bucket.get("mass_fraction") or 0.0), float(item.get("mass_fraction") or 0.0))
                bucket["frequency_weight"] = max(float(bucket.get("frequency_weight") or 0.0), float(item.get("weight") or 0.0))
                if bucket["classification"] != "true_ingredient" and classification == "true_ingredient":
                    bucket["classification"] = classification
                    bucket["reason"] = reason
                bucket["ingredient_role"] = ingredient_role if bucket.get("ingredient_role") == "other" else bucket.get("ingredient_role")
                bucket["sources"].add(source_kind)
        for payload in supports_by_label[str(label)].values():
            payload["ingredient_texts"] = sorted(payload["ingredient_texts"])
            payload["sources"] = sorted(payload["sources"])
            grams_hint = (
                float(payload.get("mass_fraction") or 0.0) * 100.0
                if float(payload.get("mass_fraction") or 0.0) > 0.0
                else float(payload.get("frequency_weight") or 0.0) * 100.0
            )
            payload["support_score"] = _score_candidate(
                label=str(label),
                source_kind="recipenlg_mass" if "recipenlg_mass" in payload["sources"] else "recipenlg_frequency",
                ingredient_role=str(payload.get("ingredient_role") or "other"),
                prevalence=float(payload.get("prevalence") or 0.0),
                source_count=int(payload.get("source_count") or 0),
                grams_per_100g=grams_hint,
                classification=str(payload.get("classification") or "ambiguous"),
            )
    return supports_by_label


def _food_role(description: str, preferred_source: str, dish_tokens: set[str]) -> str:
    lowered = _normalize_spaces(description)
    if preferred_source == "survey_fndds_food":
        return "dish"
    if dish_tokens.intersection(_match_tokens(lowered)):
        return "dish"
    if _is_user_facing_composite_text(lowered):
        return "dish"
    return "ingredient"


def _has_runtime_quality_nutrients(row: dict[str, Any]) -> bool:
    core_values = {column_name: _as_float(row.get(column_name)) for column_name in CORE_NUTRIENT_COLUMNS}
    if any(value is None for value in core_values.values()):
        return False
    all_values = {
        column_name: _as_float(row.get(column_name))
        for column_name in TARGET_NUTRIENTS
    }
    if any(value is not None and value < 0.0 for value in all_values.values()):
        return False
    if core_values["protein_g_per_100g"] > 100.0 or core_values["fat_g_per_100g"] > 100.0 or core_values["carbs_g_per_100g"] > 100.0:
        return False
    if core_values["calories_per_100g"] > 902.5:
        return False
    saturated_fat = all_values["saturated_fat_g_per_100g"]
    fiber = all_values["fiber_g_per_100g"]
    sugar = all_values["sugar_g_per_100g"]
    if saturated_fat is not None and saturated_fat > core_values["fat_g_per_100g"] + 1.0:
        return False
    if fiber is not None and fiber > core_values["carbs_g_per_100g"] + 1.0:
        return False
    if sugar is not None and sugar > core_values["carbs_g_per_100g"] + 1.0:
        return False
    return True


def _derivation_quality(code: str | None, description: str | None) -> float:
    code_text = str(code or "").strip().upper()
    description_text = str(description or "").strip().lower()
    if code_text in {"A", "AI", "AR", "PAK"} or description_text.startswith("analytical"):
        return 1.0
    if code_text == "AS":
        return 0.95
    if code_text == "Z":
        return 0.9
    if code_text in {"NC", "LCCS", "LCSA", "LCSL", "LCCD"}:
        return 0.75
    if "estimated formulation" in description_text or "calculated" in description_text:
        return 0.6
    if "based on another form" in description_text:
        return 0.55
    if not code_text and not description_text:
        return 0.5
    return 0.5


def _fndds_source_quality(source_text: str | None, derivation_code: str | None) -> float:
    lowered = str(source_text or "").strip().lower()
    code_text = str(derivation_code or "").strip().upper()
    if "sr legacy" in lowered or "foundation" in lowered:
        return 0.85
    if "analytical" in lowered:
        return 1.0
    if code_text == "NC":
        return 0.75
    return 0.7


def _create_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = MEMORY")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute("PRAGMA cache_size = -200000")
    return connection


def _drop_runtime_tables(connection: sqlite3.Connection) -> None:
    tables = (
        "meal_log_ingredients",
        "consumption_logs",
        "serving_sizes",
        "barcodes",
        "products",
        "user_food_corrections",
        "dish_aliases",
        "dish_variant_evidence",
        "dish_ingredient_priors",
        "dish_labels",
        "food_portion_priors",
        "canonical_food_aliases",
        "canonical_food_nutrients",
        "unknown_foods",
        "unknown_barcodes",
        "canonical_foods",
        "nutrition_sources",
    )
    for table in tables:
        connection.execute(f"DROP TABLE IF EXISTS {_q(table)}")


def _drop_duplicate_plural_raw_tables(connection: sqlite3.Connection) -> None:
    for table in ("usda_food_portions", "usda_measure_units"):
        connection.execute(f"DROP TABLE IF EXISTS {_q(table)}")


def _create_runtime_schema(connection: sqlite3.Connection) -> None:
    _drop_runtime_tables(connection)
    _drop_duplicate_plural_raw_tables(connection)
    connection.executescript(
        """
        CREATE TABLE canonical_foods (
            canonical_food_id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT NOT NULL,
            prep_state TEXT NOT NULL,
            food_role TEXT NOT NULL,
            preferred_fdc_id INTEGER,
            preferred_source TEXT NOT NULL,
            food_category_id INTEGER,
            confidence_score REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE canonical_food_aliases (
            alias_name TEXT PRIMARY KEY,
            canonical_food_id INTEGER NOT NULL,
            alias_type TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (canonical_food_id) REFERENCES canonical_foods(canonical_food_id)
        );

        CREATE TABLE canonical_food_nutrients (
            canonical_food_id INTEGER PRIMARY KEY,
            calories_per_100g REAL,
            protein_g_per_100g REAL,
            fat_g_per_100g REAL,
            saturated_fat_g_per_100g REAL,
            carbs_g_per_100g REAL,
            fiber_g_per_100g REAL,
            sugar_g_per_100g REAL,
            sodium_mg_per_100g REAL,
            cholesterol_mg_per_100g REAL,
            calcium_mg_per_100g REAL,
            iron_mg_per_100g REAL,
            potassium_mg_per_100g REAL,
            source_fdc_id INTEGER,
            nutrient_coverage_score REAL NOT NULL,
            derivation_quality_score REAL NOT NULL,
            FOREIGN KEY (canonical_food_id) REFERENCES canonical_foods(canonical_food_id)
        );

        CREATE TABLE nutrition_sources (
            source_fdc_id INTEGER PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_rank INTEGER NOT NULL,
            usda_description TEXT NOT NULL,
            food_category_id INTEGER,
            publication_date TEXT,
            nutrient_row_count INTEGER NOT NULL,
            nutrient_coverage_score REAL NOT NULL,
            derivation_quality_score REAL NOT NULL,
            evidence_table TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE food_portion_priors (
            portion_prior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_food_id INTEGER NOT NULL,
            source_fdc_id INTEGER NOT NULL,
            amount REAL,
            measure_unit_id INTEGER,
            measure_unit_name TEXT,
            portion_description TEXT,
            modifier TEXT,
            gram_weight REAL NOT NULL,
            data_points INTEGER,
            source TEXT NOT NULL,
            confidence_score REAL NOT NULL,
            FOREIGN KEY (canonical_food_id) REFERENCES canonical_foods(canonical_food_id)
        );

        CREATE TABLE dish_labels (
            dish_key TEXT PRIMARY KEY,
            dish_label TEXT NOT NULL,
            display_name TEXT NOT NULL,
            canonical_food_id INTEGER,
            source TEXT NOT NULL,
            confidence_score REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (canonical_food_id) REFERENCES canonical_foods(canonical_food_id)
        );

        CREATE TABLE dish_aliases (
            alias_name TEXT PRIMARY KEY,
            dish_key TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (dish_key) REFERENCES dish_labels(dish_key)
        );

        CREATE TABLE dish_variant_evidence (
            evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            dish_key TEXT NOT NULL,
            source TEXT NOT NULL,
            source_fdc_id INTEGER,
            source_description TEXT NOT NULL,
            normalized_description TEXT,
            match_score REAL,
            evidence_type TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (dish_key) REFERENCES dish_labels(dish_key)
        );

        CREATE TABLE dish_ingredient_priors (
            dish_prior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            dish_key TEXT NOT NULL,
            canonical_ingredient_id INTEGER NOT NULL,
            mean_grams_per_100g_dish REAL NOT NULL,
            median_grams_per_100g_dish REAL NOT NULL,
            mode_grams_per_100g_dish REAL NOT NULL,
            prevalence REAL NOT NULL,
            recipe_count INTEGER NOT NULL,
            source TEXT NOT NULL,
            ingredient_role TEXT,
            confidence_score REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (dish_key) REFERENCES dish_labels(dish_key),
            FOREIGN KEY (canonical_ingredient_id) REFERENCES canonical_foods(canonical_food_id),
            UNIQUE (dish_key, canonical_ingredient_id, source)
        );

        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_fdc_id INTEGER UNIQUE,
            brand TEXT,
            brand_owner TEXT,
            brand_name TEXT,
            product_name TEXT NOT NULL,
            category TEXT,
            source TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            raw_nutrition_json TEXT,
            publication_date TEXT,
            household_serving_fulltext TEXT,
            calories_per_100g REAL,
            protein_g_per_100g REAL,
            total_fat_g_per_100g REAL,
            saturated_fat_g_per_100g REAL,
            total_carbs_g_per_100g REAL,
            fiber_g_per_100g REAL,
            total_sugars_g_per_100g REAL,
            sodium_mg_per_100g REAL,
            cholesterol_mg_per_100g REAL
        );

        CREATE TABLE barcodes (
            barcode TEXT PRIMARY KEY,
            product_id INTEGER NOT NULL,
            source_fdc_id INTEGER,
            publication_date TEXT,
            format TEXT,
            verified INTEGER DEFAULT 0,
            source TEXT DEFAULT 'usda_branded',
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        );

        CREATE TABLE serving_sizes (
            serving_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            serving_name TEXT,
            grams REAL NOT NULL,
            measure_unit_name TEXT,
            household_serving_fulltext TEXT,
            is_default INTEGER DEFAULT 0,
            source TEXT DEFAULT 'usda_branded',
            FOREIGN KEY (product_id) REFERENCES products(product_id)
        );

        CREATE TABLE consumption_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            ref_id TEXT,
            canonical_food_id INTEGER,
            dish_key TEXT,
            product_id INTEGER,
            grams_consumed REAL NOT NULL,
            consumed_at TEXT NOT NULL,
            calories REAL,
            protein_g REAL,
            total_fat_g REAL,
            total_carbs_g REAL,
            fiber_g REAL,
            total_sugars_g REAL,
            sodium_mg REAL,
            cholesterol_mg REAL,
            confidence REAL DEFAULT 1.0,
            flags TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE meal_log_ingredients (
            meal_log_ingredient_id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_id INTEGER NOT NULL,
            canonical_food_id INTEGER,
            ingredient_name TEXT NOT NULL,
            matched_ingredient TEXT,
            included_in_nutrition INTEGER DEFAULT 1,
            grams_in_meal REAL NOT NULL,
            grams_per_100g_dish REAL,
            calories REAL,
            protein_g REAL,
            total_fat_g REAL,
            total_carbs_g REAL,
            fiber_g REAL,
            total_sugars_g REAL,
            sodium_mg REAL,
            cholesterol_mg REAL,
            source_fdc_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (log_id) REFERENCES consumption_logs(log_id),
            FOREIGN KEY (canonical_food_id) REFERENCES canonical_foods(canonical_food_id)
        );

        CREATE TABLE unknown_foods (
            unknown_food_id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_name TEXT NOT NULL UNIQUE,
            normalized_name TEXT,
            first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
            sightings INTEGER DEFAULT 1
        );

        CREATE TABLE unknown_barcodes (
            barcode TEXT PRIMARY KEY,
            first_seen TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE user_food_corrections (
            correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_name TEXT NOT NULL,
            corrected_dish_key TEXT,
            corrected_canonical_food_id INTEGER,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (corrected_dish_key) REFERENCES dish_labels(dish_key),
            FOREIGN KEY (corrected_canonical_food_id) REFERENCES canonical_foods(canonical_food_id)
        );
        """
    )


def _create_runtime_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE INDEX idx_canonical_food_aliases_food ON canonical_food_aliases(canonical_food_id);
        CREATE INDEX idx_food_portion_priors_food ON food_portion_priors(canonical_food_id);
        CREATE INDEX idx_dish_ingredient_priors_dish ON dish_ingredient_priors(dish_key);
        CREATE INDEX idx_dish_aliases_dish ON dish_aliases(dish_key);
        CREATE INDEX idx_dish_variant_evidence_dish ON dish_variant_evidence(dish_key);
        CREATE INDEX idx_barcodes_product ON barcodes(product_id);
        CREATE INDEX idx_serving_sizes_product ON serving_sizes(product_id);
        CREATE INDEX idx_consumption_logs_consumed_at ON consumption_logs(consumed_at);
        CREATE INDEX idx_meal_log_ingredients_log ON meal_log_ingredients(log_id);
        """
    )


def _ensure_import_manifest(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE IF EXISTS usda_import_manifest")
    connection.execute(
        """
        CREATE TABLE usda_import_manifest (
            source_file TEXT PRIMARY KEY,
            raw_table TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            schema_hash TEXT NOT NULL,
            imported_at TEXT NOT NULL
        )
        """
    )


def _validate_required_columns(filename: str, header: list[str]) -> None:
    columns = set(header)
    for column in DOWNSTREAM_REQUIRED_COLUMNS.get(filename, ()):
        if column not in columns:
            raise ValueError(f"Required downstream column missing in {filename}: {column}")


def _import_csv_as_raw_table(connection: sqlite3.Connection, source_path: Path, raw_table: str) -> dict[str, Any]:
    with source_path.open("r", encoding="utf-8", newline="", errors="replace") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        if not header:
            raise ValueError(f"CSV header missing for source file: {source_path.name}")
        _validate_required_columns(source_path.name, header)
        connection.execute(f"DROP TABLE IF EXISTS {_q(raw_table)}")
        columns_sql = ", ".join(f"{_q(column)} TEXT" for column in header)
        connection.execute(f"CREATE TABLE {_q(raw_table)} ({columns_sql})")
        placeholders = ", ".join("?" for _ in header)
        insert_sql = f"INSERT INTO {_q(raw_table)} ({', '.join(_q(column) for column in header)}) VALUES ({placeholders})"
        batch: list[tuple[str | None, ...]] = []
        row_count = 0
        for row in reader:
            batch.append(tuple(row.get(column) for column in header))
            row_count += 1
            if len(batch) >= 5000:
                connection.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            connection.executemany(insert_sql, batch)
    manifest_row = {
        "source_file": source_path.name,
        "raw_table": raw_table,
        "row_count": row_count,
        "column_count": len(header),
        "schema_hash": _schema_hash(header),
        "imported_at": _utc_now(),
    }
    connection.execute(
        """
        INSERT INTO usda_import_manifest (
            source_file,
            raw_table,
            row_count,
            column_count,
            schema_hash,
            imported_at
        ) VALUES (
            :source_file,
            :raw_table,
            :row_count,
            :column_count,
            :schema_hash,
            :imported_at
        )
        """,
        manifest_row,
    )
    return manifest_row


def _create_raw_indexes(connection: sqlite3.Connection) -> None:
    index_statements = (
        "CREATE INDEX IF NOT EXISTS idx_usda_food_fdc_id ON usda_food(fdc_id)",
        "CREATE INDEX IF NOT EXISTS idx_usda_food_type ON usda_food(data_type)",
        "CREATE INDEX IF NOT EXISTS idx_usda_food_nutrient_food_nutrient ON usda_food_nutrient(fdc_id, nutrient_id)",
        "CREATE INDEX IF NOT EXISTS idx_usda_food_nutrient_nutrient_food ON usda_food_nutrient(nutrient_id, fdc_id)",
        "CREATE INDEX IF NOT EXISTS idx_usda_food_portion_food ON usda_food_portion(fdc_id)",
        "CREATE INDEX IF NOT EXISTS idx_usda_foundation_food_fdc_id ON usda_foundation_food(fdc_id)",
        "CREATE INDEX IF NOT EXISTS idx_usda_sr_legacy_food_fdc_id ON usda_sr_legacy_food(fdc_id)",
        "CREATE INDEX IF NOT EXISTS idx_usda_survey_fndds_food_fdc_id ON usda_survey_fndds_food(fdc_id)",
        "CREATE INDEX IF NOT EXISTS idx_usda_input_food_food ON usda_input_food(fdc_id)",
        "CREATE INDEX IF NOT EXISTS idx_usda_input_food_input ON usda_input_food(fdc_id_of_input_food)",
        "CREATE INDEX IF NOT EXISTS idx_usda_branded_food_upc ON usda_branded_food(gtin_upc)",
        "CREATE INDEX IF NOT EXISTS idx_usda_branded_food_fdc_id ON usda_branded_food(fdc_id)",
    )
    for statement in index_statements:
        connection.execute(statement)


def import_required_usda_files(connection: sqlite3.Connection, data_dir: Path) -> dict[str, dict[str, Any]]:
    _ensure_import_manifest(connection)
    summary: dict[str, dict[str, Any]] = {}
    for filename, raw_table in RAW_SOURCE_FILES:
        source_path = _required_source_path(data_dir, filename)
        if not source_path.exists():
            raise FileNotFoundError(f"Missing required USDA source file: {source_path}")
        summary[raw_table] = _import_csv_as_raw_table(connection, source_path, raw_table)
    _create_raw_indexes(connection)
    return summary


def _load_nutrient_map(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
    rows = connection.execute(
        "SELECT id, name, unit_name, nutrient_nbr FROM usda_nutrient"
    ).fetchall()
    by_nbr: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_nbr[str(row["nutrient_nbr"])] .append(row)
    result: dict[str, dict[str, str]] = {}
    for column_name, config in TARGET_NUTRIENTS.items():
        candidates = [
            row
            for row in by_nbr.get(config["nutrient_nbr"], [])
            if str(row["unit_name"]).upper() == config["unit_name"]
        ]
        if config["preferred_name"]:
            preferred = [row for row in candidates if str(row["name"]) == config["preferred_name"]]
            if preferred:
                candidates = preferred
        if not candidates:
            raise ValueError(
                f"Could not resolve nutrient mapping for nutrient_nbr={config['nutrient_nbr']} "
                f"and target column {column_name}"
            )
        chosen = candidates[0]
        result[column_name] = {
            "nutrient_id": str(chosen["id"]),
            "nutrient_name": str(chosen["name"]),
            "nutrient_nbr": str(chosen["nutrient_nbr"]),
            "unit_name": str(chosen["unit_name"]),
        }
    return result


def _required_nutrient_ids(nutrient_map: dict[str, dict[str, str]]) -> list[str]:
    return [config["nutrient_id"] for config in nutrient_map.values()]


def _required_nutrient_nbrs(nutrient_map: dict[str, dict[str, str]]) -> list[str]:
    return [config["nutrient_nbr"] for config in nutrient_map.values()]


def _pivot_case_sql(source_column: str, pivot_value: str, alias_name: str) -> str:
    return f"MAX(CASE WHEN {source_column} = '{pivot_value}' THEN amount END) AS {_q(alias_name)}"


def _build_direct_source_rows(connection: sqlite3.Connection, nutrient_map: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    nutrient_ids = _required_nutrient_ids(nutrient_map)
    pivot_sql = ",\n                ".join(
        _pivot_case_sql("nutrient_id", mapping["nutrient_id"], column_name)
        for column_name, mapping in nutrient_map.items()
    )
    nutrient_id_list = ", ".join(f"'{nutrient_id}'" for nutrient_id in nutrient_ids)
    query = f"""
        WITH source_foods AS (
            SELECT f.fdc_id, 'foundation_food' AS preferred_source, 1 AS source_rank, f.description, f.food_category_id, f.publication_date
            FROM usda_food f
            INNER JOIN usda_foundation_food s ON s.fdc_id = f.fdc_id
            UNION ALL
            SELECT f.fdc_id, 'sr_legacy_food' AS preferred_source, 2 AS source_rank, f.description, f.food_category_id, f.publication_date
            FROM usda_food f
            INNER JOIN usda_sr_legacy_food s ON s.fdc_id = f.fdc_id
            UNION ALL
            SELECT f.fdc_id, 'survey_fndds_food' AS preferred_source, 3 AS source_rank, f.description, f.food_category_id, f.publication_date
            FROM usda_food f
            INNER JOIN usda_survey_fndds_food s ON s.fdc_id = f.fdc_id
        ),
        nutrient_rows AS (
            SELECT
                fn.fdc_id AS fdc_id,
                fn.nutrient_id AS nutrient_id,
                CAST(fn.amount AS REAL) AS amount,
                fn.derivation_id AS derivation_id,
                d.code AS derivation_code,
                d.description AS derivation_description
            FROM source_foods sf
            INNER JOIN usda_food_nutrient fn ON fn.fdc_id = sf.fdc_id
            LEFT JOIN usda_food_nutrient_derivation d ON d.id = fn.derivation_id
            WHERE fn.nutrient_id IN ({nutrient_id_list})
        ),
        nutrient_pivot AS (
            SELECT
                fdc_id,
                {pivot_sql},
                SUM(CASE WHEN amount IS NOT NULL THEN 1 ELSE 0 END) AS nutrient_row_count,
                GROUP_CONCAT(DISTINCT derivation_id) AS derivation_ids,
                GROUP_CONCAT(DISTINCT COALESCE(derivation_code, '')) AS derivation_codes,
                GROUP_CONCAT(DISTINCT COALESCE(derivation_description, '')) AS derivation_descriptions
            FROM nutrient_rows
            GROUP BY fdc_id
        )
        SELECT
            sf.fdc_id,
            sf.preferred_source,
            sf.source_rank,
            sf.description,
            sf.food_category_id,
            sf.publication_date,
            np.*,
            fc.description AS food_category_description
        FROM source_foods sf
        INNER JOIN nutrient_pivot np ON np.fdc_id = sf.fdc_id
        LEFT JOIN usda_food_category fc ON fc.id = sf.food_category_id
        ORDER BY sf.source_rank, sf.description
    """
    rows = connection.execute(query).fetchall()
    dish_tokens = {
        token
        for label in _load_class_labels()
        for token in label.lower().replace("_", " ").split()
    }
    result: list[dict[str, Any]] = []
    for row in rows:
        derivation_codes = [code for code in str(row["derivation_codes"] or "").split(",") if code]
        derivation_descriptions = [text for text in str(row["derivation_descriptions"] or "").split(",") if text]
        derivation_quality_values = [
            _derivation_quality(code, description)
            for code, description in zip(
                derivation_codes or [""],
                derivation_descriptions or [""],
            )
        ]
        coverage_count = sum(
            1 for column_name in TARGET_NUTRIENTS if _as_float(row[column_name]) is not None
        )
        coverage_score = round(coverage_count / float(len(TARGET_NUTRIENTS)), 6)
        derivation_quality_score = round(
            sum(derivation_quality_values) / max(1, len(derivation_quality_values)),
            6,
        )
        result.append(
            {
                "fdc_id": int(row["fdc_id"]),
                "preferred_source": str(row["preferred_source"]),
                "source_rank": int(row["source_rank"]),
                "description": str(row["description"]),
                "food_category_id": int(row["food_category_id"]) if str(row["food_category_id"] or "").isdigit() else None,
                "publication_date": str(row["publication_date"] or ""),
                "food_category_description": str(row["food_category_description"] or ""),
                "prep_state": _extract_prep_state(str(row["description"])),
                "food_role": _food_role(str(row["description"]), str(row["preferred_source"]), dish_tokens),
                "nutrient_row_count": int(row["nutrient_row_count"] or 0),
                "nutrient_coverage_score": coverage_score,
                "derivation_quality_score": derivation_quality_score,
                **{
                    column_name: _as_float(row[column_name])
                    for column_name in TARGET_NUTRIENTS
                },
            }
        )
    return result


def _build_fndds_candidate_rows(connection: sqlite3.Connection, nutrient_map: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    nutrient_nbrs = _required_nutrient_nbrs(nutrient_map)
    pivot_sql = ",\n                ".join(
        f"MAX(CASE WHEN nutrient_code = '{mapping['nutrient_nbr']}' THEN amount END) AS {_q(column_name)}"
        for column_name, mapping in nutrient_map.items()
    )
    nutrient_nbr_list = ", ".join(f"'{nutrient_nbr}'" for nutrient_nbr in nutrient_nbrs)
    query = f"""
        WITH source_rows AS (
            SELECT
                {_q("FDC ID")} AS fdc_id,
                {_q("Ingredient description")} AS description,
                {_q("Nutrient code")} AS nutrient_code,
                CAST({_q("Nutrient value")} AS REAL) AS amount,
                {_q("Start date")} AS start_date,
                {_q("End date")} AS end_date,
                {_q("Nutrient value source")} AS nutrient_value_source,
                {_q("Derivation code")} AS derivation_code
            FROM usda_fndds_ingredient_nutrient_value
            WHERE {_q("Nutrient code")} IN ({nutrient_nbr_list})
        )
        SELECT
            fdc_id,
            description,
            {pivot_sql},
            MIN(start_date) AS start_date,
            MAX(end_date) AS end_date,
            GROUP_CONCAT(DISTINCT COALESCE(nutrient_value_source, '')) AS nutrient_value_sources,
            GROUP_CONCAT(DISTINCT COALESCE(derivation_code, '')) AS derivation_codes
        FROM source_rows
        GROUP BY fdc_id, description
        ORDER BY description
    """
    rows = connection.execute(query).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        source_values = [value for value in str(row["nutrient_value_sources"] or "").split(",") if value]
        derivation_codes = [value for value in str(row["derivation_codes"] or "").split(",") if value]
        quality_values = [
            _fndds_source_quality(
                source_values[index] if index < len(source_values) else "",
                derivation_codes[index] if index < len(derivation_codes) else "",
            )
            for index in range(max(len(source_values), len(derivation_codes), 1))
        ]
        coverage_count = sum(
            1 for column_name in TARGET_NUTRIENTS if _as_float(row[column_name]) is not None
        )
        result.append(
            {
                "fdc_id": int(row["fdc_id"]) if str(row["fdc_id"] or "").isdigit() else None,
                "description": str(row["description"]),
                "preferred_source": "fndds_ingredient_nutrient_value",
                "source_rank": 3,
                "food_category_id": None,
                "publication_date": str(row["end_date"] or row["start_date"] or ""),
                "prep_state": _extract_prep_state(str(row["description"])),
                "food_role": "ingredient",
                "nutrient_row_count": coverage_count,
                "nutrient_coverage_score": round(coverage_count / float(len(TARGET_NUTRIENTS)), 6),
                "derivation_quality_score": round(sum(quality_values) / max(1, len(quality_values)), 6),
                **{
                    column_name: _as_float(row[column_name])
                    for column_name in TARGET_NUTRIENTS
                },
            }
        )
    return result


def _insert_canonical_food_row(connection: sqlite3.Connection, row: dict[str, Any]) -> int:
    now = _utc_now()
    cursor = connection.execute(
        """
        INSERT INTO canonical_foods (
            canonical_name,
            prep_state,
            food_role,
            preferred_fdc_id,
            preferred_source,
            food_category_id,
            confidence_score,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(row["canonical_name"]),
            str(row["prep_state"]),
            str(row["food_role"]),
            row["preferred_fdc_id"],
            str(row["preferred_source"]),
            row["food_category_id"],
            float(row["confidence_score"]),
            now,
            now,
        ),
    )
    canonical_food_id = int(cursor.lastrowid)
    connection.execute(
        """
        INSERT INTO canonical_food_nutrients (
            canonical_food_id,
            calories_per_100g,
            protein_g_per_100g,
            fat_g_per_100g,
            saturated_fat_g_per_100g,
            carbs_g_per_100g,
            fiber_g_per_100g,
            sugar_g_per_100g,
            sodium_mg_per_100g,
            cholesterol_mg_per_100g,
            calcium_mg_per_100g,
            iron_mg_per_100g,
            potassium_mg_per_100g,
            source_fdc_id,
            nutrient_coverage_score,
            derivation_quality_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            canonical_food_id,
            row.get("calories_per_100g"),
            row.get("protein_g_per_100g"),
            row.get("fat_g_per_100g"),
            row.get("saturated_fat_g_per_100g"),
            row.get("carbs_g_per_100g"),
            row.get("fiber_g_per_100g"),
            row.get("sugar_g_per_100g"),
            row.get("sodium_mg_per_100g"),
            row.get("cholesterol_mg_per_100g"),
            row.get("calcium_mg_per_100g"),
            row.get("iron_mg_per_100g"),
            row.get("potassium_mg_per_100g"),
            row.get("preferred_fdc_id"),
            row.get("nutrient_coverage_score"),
            row.get("derivation_quality_score"),
        ),
    )
    if row.get("preferred_fdc_id") is not None:
        connection.execute(
            """
            INSERT OR REPLACE INTO nutrition_sources (
                source_fdc_id,
                source_type,
                source_rank,
                usda_description,
                food_category_id,
                publication_date,
                nutrient_row_count,
                nutrient_coverage_score,
                derivation_quality_score,
                evidence_table,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("preferred_fdc_id"),
                row.get("preferred_source"),
                row.get("source_rank"),
                row.get("source_description") or row.get("canonical_name"),
                row.get("food_category_id"),
                row.get("publication_date"),
                row.get("nutrient_row_count") or 0,
                row.get("nutrient_coverage_score") or 0.0,
                row.get("derivation_quality_score") or 0.0,
                row.get("evidence_table") or row.get("preferred_source"),
                now,
            ),
        )
    return canonical_food_id


def _insert_direct_canonical_rows(connection: sqlite3.Connection, rows: list[dict[str, Any]]) -> dict[int, int]:
    fdc_to_canonical_id: dict[int, int] = {}
    for row in rows:
        if not _has_runtime_quality_nutrients(row):
            continue
        canonical_food_id = _insert_canonical_food_row(
            connection,
            {
                "canonical_name": _normalize_spaces(row["description"]),
                "prep_state": row["prep_state"],
                "food_role": row["food_role"],
                "preferred_fdc_id": row["fdc_id"],
                "preferred_source": row["preferred_source"],
                "food_category_id": row["food_category_id"],
                "confidence_score": round(
                    min(
                        1.0,
                        (1.05 - (0.05 * max(0, row["source_rank"] - 1))) * row["nutrient_coverage_score"],
                    ),
                    6,
                ),
                "source_description": row["description"],
                "publication_date": row["publication_date"],
                "nutrient_row_count": row["nutrient_row_count"],
                "nutrient_coverage_score": row["nutrient_coverage_score"],
                "derivation_quality_score": row["derivation_quality_score"],
                "source_rank": row["source_rank"],
                "evidence_table": row["preferred_source"],
                **{column_name: row.get(column_name) for column_name in TARGET_NUTRIENTS},
            },
        )
        fdc_to_canonical_id[row["fdc_id"]] = canonical_food_id
    return fdc_to_canonical_id


def _load_catalog_frame(connection: sqlite3.Connection) -> pd.DataFrame:
    query = """
        SELECT
            cf.canonical_food_id,
            cf.canonical_name,
            cf.prep_state,
            cf.food_role,
            cf.preferred_fdc_id AS fdc_id,
            cf.preferred_source,
            cf.confidence_score,
            ns.usda_description AS description,
            cf.food_category_id
        FROM canonical_foods cf
        LEFT JOIN nutrition_sources ns
            ON ns.source_fdc_id = cf.preferred_fdc_id
        ORDER BY cf.preferred_source, cf.canonical_food_id
    """
    return pd.read_sql_query(query, connection)


def _candidate_frame_for_source(catalog: pd.DataFrame, source_name: str, requested_prep: str | None) -> pd.DataFrame:
    frame = catalog[catalog["preferred_source"] == source_name].copy()
    if requested_prep:
        preferred = frame[frame["prep_state"].astype(str).str.lower() == requested_prep.lower()].copy()
        if not preferred.empty:
            frame = preferred
    return frame


def _detect_requested_prep(text: str) -> str | None:
    lowered = _normalize_spaces(text)
    for prep_state, phrases in PREP_STATE_PRIORITY:
        if prep_state in lowered:
            return prep_state
        if any(_normalize_spaces(phrase) in lowered for phrase in phrases):
            return prep_state
    return None


def _pick_best_match(frame: pd.DataFrame, target: str) -> dict[str, Any] | None:
    if frame.empty:
        return None
    requested = normalize_ingredient(target) or _normalize_spaces(target)
    candidate_frame = frame
    anchor_tokens = sorted(_required_tokens(requested) or _match_tokens(requested), key=len, reverse=True)
    if anchor_tokens:
        description_series = candidate_frame["description"].astype(str).str.lower()
        for token in anchor_tokens[:2]:
            filtered = candidate_frame[description_series.str.contains(rf"\b{re.escape(token)}\b", regex=True, na=False)].copy()
            if filtered.empty:
                continue
            candidate_frame = filtered
            description_series = candidate_frame["description"].astype(str).str.lower()

    best_key: tuple[float, int, float, int] | None = None
    best_payload: dict[str, Any] | None = None
    for row in candidate_frame.itertuples(index=False):
        description = str(getattr(row, "description", "") or "")
        if not _has_required_tokens(description, requested):
            continue
        score = _lexical_match_score(requested, description)
        if score <= 0.0:
            continue
        source_rank = int(getattr(row, "source_rank", 99) or 99)
        confidence_score = float(getattr(row, "confidence_score", 0.0) or 0.0)
        candidate_key = (score, -source_rank, confidence_score, -len(description))
        if best_key is None or candidate_key > best_key:
            best_key = candidate_key
            best_payload = row._asdict()
    return best_payload


def _best_catalog_match(
    catalog: pd.DataFrame,
    alias_name: str,
) -> dict[str, Any] | None:
    requested_prep = _detect_requested_prep(alias_name)
    target = normalize_ingredient(alias_name) or _normalize_spaces(alias_name)
    for source_name in DIRECT_SOURCE_PRIORITY:
        source_frame = _candidate_frame_for_source(catalog, source_name, requested_prep)
        if source_frame.empty:
            continue
        match = _pick_best_match(source_frame, target)
        if match is not None:
            return dict(match)
    return None


def _best_explicit_catalog_match(catalog: pd.DataFrame, alias_name: str) -> dict[str, Any] | None:
    preferences = EXPLICIT_ALIAS_PREFERENCES.get(_normalize_spaces(alias_name))
    if not preferences or catalog.empty:
        return None
    for preference in preferences:
        frame = catalog[catalog["preferred_source"].isin(preference["source_names"])].copy()
        if frame.empty:
            continue
        descriptions = frame["description"].astype(str).str.lower()
        for description_substring in preference["description_substrings"]:
            matches = frame[descriptions.str.contains(re.escape(description_substring.lower()), regex=True, na=False)].copy()
            if matches.empty:
                continue
            matches = matches.sort_values(
                by=["confidence_score", "preferred_source", "canonical_food_id"],
                ascending=[False, True, True],
            )
            return dict(matches.iloc[0].to_dict())
    return None


def _best_fndds_candidate(
    candidates: pd.DataFrame,
    alias_name: str,
    requested_prep: str | None,
) -> dict[str, Any] | None:
    if candidates.empty:
        return None
    target = normalize_ingredient(alias_name) or _normalize_spaces(alias_name)
    frame = candidates.copy()
    if requested_prep:
        prep_matches = frame[frame["prep_state"].astype(str).str.lower() == requested_prep.lower()].copy()
        if not prep_matches.empty:
            frame = prep_matches
    match = _pick_best_match(frame, target)
    return dict(match) if match is not None else None


def _insert_alias_row(
    connection: sqlite3.Connection,
    alias_name: str,
    canonical_food_id: int,
    alias_type: str,
    source: str,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO canonical_food_aliases (
            alias_name,
            canonical_food_id,
            alias_type,
            source,
            created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            _normalize_spaces(alias_name),
            int(canonical_food_id),
            alias_type,
            source,
            _utc_now(),
        ),
    )


def _seed_target_aliases() -> set[str]:
    targets: set[str] = set(EXPLICIT_SAMPLE_ALIASES)
    targets.update(CANONICAL_INGREDIENT_ALIASES.keys())
    targets.update(CANONICAL_INGREDIENT_ALIASES.values())
    for path in (DISH_PRIORS_PATH, DISH_MASS_PRIORS_PATH):
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for root_key in ("dish_priors", "dish_mass_priors"):
            root = payload.get(root_key) or {}
            for dish_payload in root.values():
                if isinstance(dish_payload, list):
                    ingredient_rows = dish_payload
                else:
                    ingredient_rows = dish_payload.get("ingredients") or []
                for row in ingredient_rows:
                    normalized = normalize_ingredient(str(row.get("ingredient") or ""))
                    if normalized:
                        targets.add(normalized)
    for label in _load_class_labels():
        targets.add(label.replace("_", " "))
    return {_normalize_spaces(value) for value in targets if str(value).strip()}


def _build_fndds_frame(rows: list[dict[str, Any]], existing_fdc_map: dict[int, int]) -> pd.DataFrame:
    prepared: list[dict[str, Any]] = []
    for row in rows:
        prepared.append(
            {
                "canonical_food_id": existing_fdc_map.get(int(row["fdc_id"])) if row.get("fdc_id") is not None else None,
                "canonical_name": _normalize_spaces(row["description"]),
                "prep_state": row["prep_state"],
                "food_role": row["food_role"],
                "fdc_id": row["fdc_id"],
                "preferred_source": row["preferred_source"],
                "confidence_score": row["nutrient_coverage_score"],
                "description": row["description"],
                "food_category_id": row["food_category_id"],
                **{column_name: row.get(column_name) for column_name in TARGET_NUTRIENTS},
                "publication_date": row.get("publication_date"),
                "nutrient_row_count": row.get("nutrient_row_count"),
                "nutrient_coverage_score": row.get("nutrient_coverage_score"),
                "derivation_quality_score": row.get("derivation_quality_score"),
                "source_rank": row.get("source_rank"),
            }
        )
    return pd.DataFrame(prepared)


def _ensure_alias_mapping(
    connection: sqlite3.Connection,
    catalog: pd.DataFrame,
    fndds_frame: pd.DataFrame,
    existing_fdc_map: dict[int, int],
    alias_name: str,
    alias_type: str,
    source: str,
) -> int | None:
    alias_key = _normalize_spaces(alias_name)
    existing = connection.execute(
        "SELECT canonical_food_id FROM canonical_food_aliases WHERE alias_name = ?",
        (alias_key,),
    ).fetchone()
    if existing is not None:
        return int(existing["canonical_food_id"])

    explicit_match = _best_explicit_catalog_match(catalog, alias_key)
    if explicit_match is not None:
        canonical_food_id = int(explicit_match["canonical_food_id"])
        _insert_alias_row(connection, alias_key, canonical_food_id, alias_type, source)
        return canonical_food_id

    requested_prep = _detect_requested_prep(alias_key)
    direct_match = _best_catalog_match(catalog, alias_key)
    if direct_match is not None:
        canonical_food_id = int(direct_match["canonical_food_id"])
        _insert_alias_row(connection, alias_key, canonical_food_id, alias_type, source)
        return canonical_food_id

    fndds_match = _best_fndds_candidate(fndds_frame, alias_key, requested_prep)
    if fndds_match is None:
        return None

    existing_canonical_food_id = _as_int(fndds_match.get("canonical_food_id"))
    if existing_canonical_food_id is not None:
        canonical_food_id = existing_canonical_food_id
    else:
        if not _has_runtime_quality_nutrients(fndds_match):
            return None
        preferred_fdc_id = _as_int(fndds_match.get("fdc_id"))
        if preferred_fdc_id is None:
            return None
        canonical_food_id = _insert_canonical_food_row(
            connection,
            {
                "canonical_name": alias_key,
                "prep_state": str(fndds_match["prep_state"]),
                "food_role": "ingredient",
                "preferred_fdc_id": preferred_fdc_id,
                "preferred_source": "fndds_ingredient_nutrient_value",
                "food_category_id": None,
                "confidence_score": round(float(fndds_match.get("nutrient_coverage_score") or 0.0), 6),
                "source_description": str(fndds_match["description"]),
                "publication_date": str(fndds_match.get("publication_date") or ""),
                "nutrient_row_count": int(fndds_match.get("nutrient_row_count") or 0),
                "nutrient_coverage_score": float(fndds_match.get("nutrient_coverage_score") or 0.0),
                "derivation_quality_score": float(fndds_match.get("derivation_quality_score") or 0.0),
                "source_rank": 3,
                "evidence_table": "fndds_ingredient_nutrient_value",
                **{column_name: _as_float(fndds_match.get(column_name)) for column_name in TARGET_NUTRIENTS},
            },
        )
        if preferred_fdc_id is not None:
            existing_fdc_map[preferred_fdc_id] = canonical_food_id
        catalog.loc[len(catalog)] = {
            "canonical_food_id": canonical_food_id,
            "canonical_name": alias_key,
            "prep_state": str(fndds_match["prep_state"]),
            "food_role": "ingredient",
            "fdc_id": preferred_fdc_id,
            "preferred_source": "fndds_ingredient_nutrient_value",
            "confidence_score": float(fndds_match.get("nutrient_coverage_score") or 0.0),
            "description": str(fndds_match["description"]),
            "food_category_id": None,
        }
    _insert_alias_row(connection, alias_key, canonical_food_id, alias_type, source)
    return canonical_food_id


def _seed_aliases(
    connection: sqlite3.Connection,
    catalog: pd.DataFrame,
    fndds_frame: pd.DataFrame,
    existing_fdc_map: dict[int, int],
) -> dict[str, Any]:
    initial_alias_count = int(connection.execute("SELECT COUNT(*) FROM canonical_food_aliases").fetchone()[0])
    targets = sorted(_seed_target_aliases())
    for target in targets:
        canonical_food_id = _ensure_alias_mapping(
            connection,
            catalog,
            fndds_frame,
            existing_fdc_map,
            target,
            "palm_seed",
            "palm_runtime_targets",
        )
        if canonical_food_id is None:
            continue
        if canonical_food_id is not None:
            for alias in ingredient_aliases_for(target):
                _ensure_alias_mapping(
                    connection,
                    catalog,
                    fndds_frame,
                    existing_fdc_map,
                    alias,
                    "ingredient_alias",
                    "ingredient_aliases_for",
                )

    direct_rows = connection.execute(
        """
        SELECT cf.canonical_food_id, ns.usda_description
        FROM canonical_foods cf
        LEFT JOIN nutrition_sources ns ON ns.source_fdc_id = cf.preferred_fdc_id
        """
    ).fetchall()
    for row in direct_rows:
        description = _normalize_spaces(row["usda_description"])
        if not description:
            continue
        _insert_alias_row(connection, description, int(row["canonical_food_id"]), "usda_description", "usda_description")

    alias_rows = connection.execute(
        """
        SELECT a.alias_name, a.canonical_food_id, cf.prep_state
        FROM canonical_food_aliases a
        INNER JOIN canonical_foods cf ON cf.canonical_food_id = a.canonical_food_id
        """
    ).fetchall()
    for row in alias_rows:
        prep_state = str(row["prep_state"] or "").strip().lower()
        if prep_state and prep_state != "unspecified":
            _insert_alias_row(
                connection,
                f"{row['alias_name']} {prep_state}",
                int(row["canonical_food_id"]),
                "prep_state_alias",
                "prep_state_alias",
            )
            _insert_alias_row(
                connection,
                f"{prep_state} {row['alias_name']}",
                int(row["canonical_food_id"]),
                "prep_state_alias",
                "prep_state_alias",
            )

    primary_alias_rows = connection.execute(
        """
        SELECT canonical_food_id, alias_name
        FROM (
            SELECT
                canonical_food_id,
                alias_name,
                ROW_NUMBER() OVER (
                    PARTITION BY canonical_food_id
                    ORDER BY
                        (LENGTH(alias_name) - LENGTH(REPLACE(alias_name, ' ', ''))) ASC,
                        LENGTH(alias_name) ASC,
                        CASE alias_type
                            WHEN 'sample_alias' THEN 0
                            WHEN 'ingredient_alias' THEN 1
                            WHEN 'palm_seed' THEN 2
                            WHEN 'dish_display' THEN 3
                            WHEN 'dish_label' THEN 4
                            ELSE 9
                        END ASC,
                        alias_name ASC
                ) AS row_num
            FROM canonical_food_aliases
            WHERE alias_type IN ('palm_seed', 'ingredient_alias', 'dish_label', 'sample_alias', 'dish_display')
        )
        WHERE row_num = 1
        """
    ).fetchall()
    for row in primary_alias_rows:
        connection.execute(
            """
            UPDATE canonical_foods
            SET canonical_name = ?, updated_at = ?
            WHERE canonical_food_id = ?
            """,
            (str(row["alias_name"]), _utc_now(), int(row["canonical_food_id"])),
        )

    return {
        "alias_rows": int(connection.execute("SELECT COUNT(*) FROM canonical_food_aliases").fetchone()[0]),
        "seed_target_count": len(targets),
        "seed_insertions_observed": max(
            0,
            int(connection.execute("SELECT COUNT(*) FROM canonical_food_aliases").fetchone()[0]) - initial_alias_count,
        ),
    }


def _build_portion_priors(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        """
        SELECT
            cf.canonical_food_id,
            CAST(p.fdc_id AS INTEGER) AS source_fdc_id,
            CAST(p.amount AS REAL) AS amount,
            CAST(p.measure_unit_id AS INTEGER) AS measure_unit_id,
            m.name AS measure_unit_name,
            p.portion_description,
            p.modifier,
            CAST(p.gram_weight AS REAL) AS gram_weight,
            CAST(p.data_points AS INTEGER) AS data_points
        FROM usda_food_portion p
        INNER JOIN canonical_foods cf ON CAST(cf.preferred_fdc_id AS TEXT) = p.fdc_id
        LEFT JOIN usda_measure_unit m ON m.id = p.measure_unit_id
        WHERE CAST(p.gram_weight AS REAL) > 0
        ORDER BY cf.canonical_food_id, p.id
        """
    ).fetchall()
    for row in rows:
        confidence = 1.0 if (row["data_points"] or 0) > 0 else 0.8
        connection.execute(
            """
            INSERT INTO food_portion_priors (
                canonical_food_id,
                source_fdc_id,
                amount,
                measure_unit_id,
                measure_unit_name,
                portion_description,
                modifier,
                gram_weight,
                data_points,
                source,
                confidence_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["canonical_food_id"]),
                int(row["source_fdc_id"]),
                row["amount"],
                row["measure_unit_id"],
                row["measure_unit_name"],
                row["portion_description"],
                row["modifier"],
                row["gram_weight"],
                row["data_points"],
                "usda_food_portion",
                confidence,
            ),
        )
    return len(rows)


def _build_products(connection: sqlite3.Connection, nutrient_map: dict[str, dict[str, str]]) -> dict[str, int]:
    nutrient_ids = _required_nutrient_ids(nutrient_map)
    pivot_sql = ",\n                ".join(
        _pivot_case_sql("nutrient_id", mapping["nutrient_id"], column_name)
        for column_name, mapping in nutrient_map.items()
    )
    nutrient_id_list = ", ".join(f"'{nutrient_id}'" for nutrient_id in nutrient_ids)
    connection.executescript(
        """
        DROP TABLE IF EXISTS temp_branded_latest;
        DROP TABLE IF EXISTS temp_branded_nutrient_pivot;
        """
    )
    connection.execute(
        """
        CREATE TEMP TABLE temp_branded_latest AS
        WITH branded_candidates AS (
            SELECT
                b.fdc_id,
                b.gtin_upc,
                b.serving_size,
                b.serving_size_unit,
                b.household_serving_fulltext,
                b.brand_owner,
                b.brand_name,
                b.branded_food_category,
                f.description AS product_name,
                f.publication_date
            FROM usda_branded_food b
            INNER JOIN usda_food f ON f.fdc_id = b.fdc_id
            WHERE COALESCE(TRIM(b.gtin_upc), '') <> ''
        ),
        branded_latest AS (
            SELECT *
            FROM (
                SELECT
                    c.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY c.gtin_upc
                        ORDER BY c.publication_date DESC, CAST(c.fdc_id AS INTEGER) DESC
                    ) AS row_num
                FROM branded_candidates c
            )
            WHERE row_num = 1
        )
        SELECT *
        FROM branded_latest
        """
    )
    connection.execute("CREATE INDEX temp_idx_branded_latest_fdc_id ON temp_branded_latest(fdc_id)")
    connection.execute("CREATE INDEX temp_idx_branded_latest_gtin_upc ON temp_branded_latest(gtin_upc)")
    connection.execute(
        f"""
        CREATE TEMP TABLE temp_branded_nutrient_pivot AS
        WITH nutrient_rows AS (
            SELECT
                fn.fdc_id,
                fn.nutrient_id,
                CAST(fn.amount AS REAL) AS amount
            FROM temp_branded_latest b
            INNER JOIN usda_food_nutrient fn ON fn.fdc_id = b.fdc_id
            WHERE fn.nutrient_id IN ({nutrient_id_list})
        )
        SELECT
            fdc_id,
            {pivot_sql}
        FROM nutrient_rows
        GROUP BY fdc_id
        """
    )
    connection.execute(
        """
        INSERT INTO products (
            source_fdc_id,
            brand,
            brand_owner,
            brand_name,
            product_name,
            category,
            source,
            raw_nutrition_json,
            publication_date,
            household_serving_fulltext,
            calories_per_100g,
            protein_g_per_100g,
            total_fat_g_per_100g,
            saturated_fat_g_per_100g,
            total_carbs_g_per_100g,
            fiber_g_per_100g,
            total_sugars_g_per_100g,
            sodium_mg_per_100g,
            cholesterol_mg_per_100g
        )
        SELECT
            CAST(c.fdc_id AS INTEGER) AS source_fdc_id,
            NULLIF(TRIM(COALESCE(c.brand_name, c.brand_owner, '')), '') AS brand,
            c.brand_owner,
            c.brand_name,
            c.product_name,
            c.branded_food_category,
            'usda_branded',
            '{}',
            c.publication_date,
            c.household_serving_fulltext,
            np.calories_per_100g,
            np.protein_g_per_100g,
            np.fat_g_per_100g,
            np.saturated_fat_g_per_100g,
            np.carbs_g_per_100g,
            np.fiber_g_per_100g,
            np.sugar_g_per_100g,
            np.sodium_mg_per_100g,
            np.cholesterol_mg_per_100g
        FROM temp_branded_latest c
        LEFT JOIN temp_branded_nutrient_pivot np ON np.fdc_id = c.fdc_id
        ORDER BY c.gtin_upc
        """
    )
    connection.execute(
        """
        INSERT INTO barcodes (
            barcode,
            product_id,
            source_fdc_id,
            publication_date,
            format,
            verified,
            source
        )
        SELECT
            c.gtin_upc,
            p.product_id,
            CAST(c.fdc_id AS INTEGER),
            c.publication_date,
            'upc',
            1,
            'usda_branded'
        FROM temp_branded_latest c
        INNER JOIN products p ON p.source_fdc_id = CAST(c.fdc_id AS INTEGER)
        ORDER BY c.gtin_upc
        """
    )
    connection.execute(
        """
        INSERT INTO serving_sizes (
            product_id,
            serving_name,
            grams,
            measure_unit_name,
            household_serving_fulltext,
            is_default,
            source
        )
        SELECT
            p.product_id,
            COALESCE(NULLIF(c.household_serving_fulltext, ''), c.serving_size_unit),
            CAST(c.serving_size AS REAL),
            c.serving_size_unit,
            c.household_serving_fulltext,
            1,
            'usda_branded'
        FROM temp_branded_latest c
        INNER JOIN products p ON p.source_fdc_id = CAST(c.fdc_id AS INTEGER)
        WHERE CAST(c.serving_size AS REAL) > 0
        ORDER BY c.gtin_upc
        """
    )
    product_count = int(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0])
    barcode_count = int(connection.execute("SELECT COUNT(*) FROM barcodes").fetchone()[0])
    serving_count = int(connection.execute("SELECT COUNT(*) FROM serving_sizes").fetchone()[0])
    connection.executescript(
        """
        DROP TABLE IF EXISTS temp_branded_nutrient_pivot;
        DROP TABLE IF EXISTS temp_branded_latest;
        """
    )
    return {
        "products": product_count,
        "barcodes": barcode_count,
        "serving_sizes": serving_count,
    }


def _insert_dish_label_row(
    connection: sqlite3.Connection,
    dish_key: str,
    display_name: str,
    canonical_food_id: int | None,
    source: str,
    confidence_score: float,
) -> None:
    now = _utc_now()
    connection.execute(
        """
        INSERT OR REPLACE INTO dish_labels (
            dish_key,
            dish_label,
            display_name,
            canonical_food_id,
            source,
            confidence_score,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (dish_key, display_name, display_name, canonical_food_id, source, confidence_score, now, now),
    )


def _insert_dish_variant_evidence_row(
    connection: sqlite3.Connection,
    dish_key: str,
    source: str,
    source_fdc_id: int | None,
    source_description: str,
    match_score: float | None,
    evidence_type: str,
    reason: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO dish_variant_evidence (
            dish_key,
            source,
            source_fdc_id,
            source_description,
            normalized_description,
            match_score,
            evidence_type,
            reason,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dish_key,
            source,
            source_fdc_id,
            str(source_description or "").strip(),
            _normalize_spaces(source_description),
            match_score,
            evidence_type,
            reason,
            _utc_now(),
        ),
    )


def _normalize_dish_metric_rows(metric_rows: list[dict[str, Any]], metric_key: str) -> None:
    total = sum(float(row.get(metric_key) or 0.0) for row in metric_rows)
    if total <= 0.0:
        return
    for row in metric_rows:
        row[metric_key] = round(float(row.get(metric_key) or 0.0) / total * 100.0, 6)


def _collapse_dish_prior_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["canonical_ingredient_id"]), str(row["source"]))
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(row)
            continue
        for metric_key in (
            "mean_grams_per_100g_dish",
            "median_grams_per_100g_dish",
            "mode_grams_per_100g_dish",
        ):
            existing[metric_key] = round(
                float(existing.get(metric_key) or 0.0) + float(row.get(metric_key) or 0.0),
                6,
            )
        existing["prevalence"] = round(
            max(float(existing.get("prevalence") or 0.0), float(row.get("prevalence") or 0.0)),
            6,
        )
        existing["recipe_count"] = max(int(existing.get("recipe_count") or 0), int(row.get("recipe_count") or 0))
        existing["confidence_score"] = round(
            max(float(existing.get("confidence_score") or 0.0), float(row.get("confidence_score") or 0.0)),
            6,
        )
        if not existing.get("ingredient_role") and row.get("ingredient_role"):
            existing["ingredient_role"] = row.get("ingredient_role")
    return list(merged.values())


def _load_fallback_dish_mass_priors() -> dict[str, Any]:
    if not DISH_MASS_PRIORS_PATH.exists():
        return {}
    payload = json.loads(DISH_MASS_PRIORS_PATH.read_text(encoding="utf-8"))
    return payload.get("dish_mass_priors") or {}


def _load_fallback_dish_priors() -> dict[str, Any]:
    if not DISH_PRIORS_PATH.exists():
        return {}
    payload = json.loads(DISH_PRIORS_PATH.read_text(encoding="utf-8"))
    return payload.get("dish_priors") or {}


def _canonical_row_for_id(catalog: pd.DataFrame, canonical_food_id: int | None) -> dict[str, Any] | None:
    if canonical_food_id is None or catalog.empty:
        return None
    matches = catalog[catalog["canonical_food_id"] == canonical_food_id]
    if matches.empty:
        return None
    return dict(matches.iloc[0].to_dict())


def _is_user_facing_canonical_row(row: dict[str, Any] | None) -> bool:
    if row is None:
        return False
    source_name = str(row.get("preferred_source") or "").strip().lower()
    if source_name == "survey_fndds_food":
        return False
    return not _is_user_facing_composite_text(str(row.get("canonical_name") or row.get("description") or ""))


def _resolve_fndds_child_canonical_food_id(
    connection: sqlite3.Connection,
    catalog: pd.DataFrame,
    fndds_frame: pd.DataFrame,
    existing_fdc_map: dict[int, int],
    dish_key: str,
    raw_description: str,
    input_fdc_id: int | None,
    recipe_identity_aliases: set[str],
) -> tuple[int | None, str]:
    normalized_description = _normalize_spaces(raw_description)
    classification, reason, _ = _classify_ingredient_candidate(raw_description, dish_key, "fndds_input_food")
    if not normalized_description:
        return None, "empty_child_description"
    if classification != "true_ingredient":
        return None, reason

    for candidate_alias in _normalized_child_alias_candidates(raw_description, recipe_identity_aliases):
        if not candidate_alias or _is_user_facing_composite_text(candidate_alias):
            continue
        canonical_food_id = _ensure_alias_mapping(
            connection,
            catalog,
            fndds_frame,
            existing_fdc_map,
            candidate_alias,
            "fndds_input",
            f"fndds_input_food:{dish_key}",
        )
        candidate_row = _canonical_row_for_id(catalog, canonical_food_id)
        if canonical_food_id is not None and _is_user_facing_canonical_row(candidate_row):
            return canonical_food_id, "resolved_child_alias"

    if input_fdc_id is not None:
        mapped_canonical_food_id = existing_fdc_map.get(input_fdc_id)
        mapped_row = _canonical_row_for_id(catalog, mapped_canonical_food_id)
        if mapped_canonical_food_id is not None and _is_user_facing_canonical_row(mapped_row):
            return mapped_canonical_food_id, "reused_direct_fdc_mapping"
    return None, "no_user_facing_canonical_mapping"


def _build_dish_priors(
    connection: sqlite3.Connection,
    catalog: pd.DataFrame,
    fndds_frame: pd.DataFrame,
    existing_fdc_map: dict[int, int],
) -> dict[str, int]:
    labels = _load_class_labels()
    label_matchers = _label_matchers(labels)
    normalized_input_cache: dict[str, int | None] = {}
    fallback_mass_priors = _load_fallback_dish_mass_priors()
    fallback_frequency_priors = _load_fallback_dish_priors()
    recipe_identity_aliases_by_label = _load_recipe_identity_aliases_by_label(
        fallback_mass_priors,
        fallback_frequency_priors,
    )
    recipe_supports_by_label = _load_recipe_candidate_supports(
        fallback_mass_priors,
        fallback_frequency_priors,
    )
    for label in labels:
        canonical_food_id = _ensure_alias_mapping(
            connection,
            catalog,
            fndds_frame,
            existing_fdc_map,
            label.replace("_", " "),
            "dish_label",
            "food101_label",
        )
        _insert_dish_label_row(
            connection,
            label,
            label.replace("_", " ").title(),
            canonical_food_id,
            "food101",
            0.5,
        )
        _insert_alias_row(connection, label.replace("_", " "), canonical_food_id or 0, "dish_display", "food101") if canonical_food_id else None
        connection.execute(
            """
            INSERT OR REPLACE INTO dish_aliases (alias_name, dish_key, source, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (label.replace("_", " "), label, "food101", _utc_now()),
        )

    input_rows = connection.execute(
        """
        SELECT
            f.description AS dish_description,
            i.fdc_id AS dish_fdc_id,
            i.fdc_id_of_input_food,
            i.sr_description,
            CAST(i.gram_weight AS REAL) AS gram_weight
        FROM usda_input_food i
        INNER JOIN usda_food f ON f.fdc_id = i.fdc_id
        INNER JOIN usda_survey_fndds_food s ON s.fdc_id = i.fdc_id
        WHERE CAST(i.gram_weight AS REAL) > 0
        ORDER BY i.fdc_id, i.seq_num
        """
    ).fetchall()

    grouped_inputs: dict[str, list[sqlite3.Row]] = defaultdict(list)
    dish_descriptions: dict[str, str] = {}
    for row in input_rows:
        dish_fdc_id = str(row["dish_fdc_id"])
        grouped_inputs[dish_fdc_id].append(row)
        dish_descriptions[dish_fdc_id] = str(row["dish_description"] or "")

    observations: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    ingredient_presence: dict[str, Counter[int]] = defaultdict(Counter)
    dish_counts: Counter[str] = Counter()
    composite_filtered_count = 0
    ingredient_candidates_scanned = 0
    true_ingredients_kept = 0
    rejected_reasons: Counter[str] = Counter()

    for dish_fdc_id, rows in grouped_inputs.items():
        title_text = f" {dish_descriptions[dish_fdc_id].lower().replace('-', ' ')} "
        title_tokens = set(title_text.split())
        matched_labels = [
            label
            for label, label_phrase, label_tokens in label_matchers
            if label_tokens.intersection(title_tokens)
            and _title_matches_dish(title_text, title_tokens, label_phrase, label_tokens)
        ]
        if not matched_labels:
            continue
        label_use_decisions = {
            label: _should_use_parent_variant(label, dish_descriptions[dish_fdc_id])
            for label in matched_labels
        }
        active_labels = [label for label, (use_children, _, _) in label_use_decisions.items() if use_children]
        for label in matched_labels:
            use_children, parent_score, parent_reason = label_use_decisions[label]
            _insert_dish_variant_evidence_row(
                connection,
                label,
                "survey_fndds_food",
                int(dish_fdc_id),
                dish_descriptions[dish_fdc_id],
                parent_score,
                "matched_parent_variant",
                parent_reason,
            )
        total_grams = sum(float(row["gram_weight"] or 0.0) for row in rows)
        if total_grams <= 0.0:
            continue
        ingredient_rows: list[tuple[int, float]] = []
        for row in rows:
            ingredient_candidates_scanned += 1
            input_fdc_id = int(row["fdc_id_of_input_food"]) if str(row["fdc_id_of_input_food"] or "").isdigit() else None
            raw_description = str(row["sr_description"] or "")
            candidate_classification, candidate_reason, _ = _classify_ingredient_candidate(
                raw_description,
                matched_labels[0],
                "fndds_input_food",
            )

            normalized_description = normalize_ingredient(raw_description)
            if normalized_description and normalized_description in normalized_input_cache:
                canonical_food_id = normalized_input_cache[normalized_description]
                resolution_reason = "normalized_input_cache"
            else:
                recipe_identity_aliases = set()
                for label in matched_labels:
                    recipe_identity_aliases.update(recipe_identity_aliases_by_label.get(label) or set())
                canonical_food_id, resolution_reason = _resolve_fndds_child_canonical_food_id(
                    connection,
                    catalog,
                    fndds_frame,
                    existing_fdc_map,
                    matched_labels[0],
                    raw_description,
                    input_fdc_id,
                    recipe_identity_aliases,
                )
                if normalized_description:
                    normalized_input_cache[normalized_description] = canonical_food_id

            if canonical_food_id is None:
                composite_filtered_count += 1
                rejected_reasons[candidate_reason or resolution_reason] += 1
                for label in matched_labels:
                    use_children, _, parent_reason = label_use_decisions[label]
                    _insert_dish_variant_evidence_row(
                        connection,
                        label,
                        "fndds_input_food",
                        input_fdc_id or int(dish_fdc_id),
                        raw_description,
                        None,
                        candidate_classification if candidate_classification != "true_ingredient" else "ambiguous",
                        candidate_reason if not use_children else resolution_reason or parent_reason,
                    )
                continue
            if input_fdc_id is not None:
                existing_fdc_map[input_fdc_id] = canonical_food_id
            ingredient_rows.append((canonical_food_id, float(row["gram_weight"])))
            true_ingredients_kept += 1
        if not ingredient_rows:
            continue

        grams_per_100g = [
            (canonical_food_id, grams / total_grams * 100.0)
            for canonical_food_id, grams in ingredient_rows
            if grams > 0.0
        ]
        if not grams_per_100g:
            continue
        for label in active_labels:
            dish_counts[label] += 1
            seen_in_dish: set[int] = set()
            for canonical_food_id, grams in grams_per_100g:
                observations[label][canonical_food_id].append(round(grams, 6))
                if canonical_food_id not in seen_in_dish:
                    ingredient_presence[label][canonical_food_id] += 1
                    seen_in_dish.add(canonical_food_id)

    dish_label_count = 0
    dish_prior_count = 0
    for label in labels:
        display_name = label.replace("_", " ").title()
        has_fndds = bool(observations.get(label))
        if has_fndds:
            _insert_dish_label_row(
                connection,
                label,
                display_name,
                connection.execute(
                    "SELECT canonical_food_id FROM dish_labels WHERE dish_key = ?",
                    (label,),
                ).fetchone()["canonical_food_id"],
                "fndds_input_food",
                0.95,
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO dish_aliases (alias_name, dish_key, source, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (display_name.lower(), label, "food101_display", _utc_now()),
            )
            rows_to_insert: list[dict[str, Any]] = []
            for canonical_food_id, values in observations[label].items():
                canonical_row = _canonical_row_for_id(catalog, canonical_food_id) or {}
                canonical_name = str(canonical_row.get("canonical_name") or "")
                recipe_support = _best_recipe_support(recipe_supports_by_label.get(label) or {}, canonical_name)
                fndds_prevalence = ingredient_presence[label][canonical_food_id] / max(1, dish_counts[label])
                support_prevalence = float(recipe_support.get("prevalence") or 0.0) if recipe_support else 0.0
                source_count = int(recipe_support.get("source_count") or 0) if recipe_support else int(ingredient_presence[label][canonical_food_id])
                ingredient_role = str(
                    (recipe_support or {}).get("ingredient_role")
                    or _ingredient_role(canonical_name)
                )
                blended_prevalence = min(
                    1.0,
                    (support_prevalence * RECIPE_SUPPORT_BLEND_WEIGHT)
                    + (fndds_prevalence * FNDDS_PREVALENCE_BLEND_WEIGHT),
                )
                candidate_score = _score_candidate(
                    label=label,
                    source_kind="fndds_input_food",
                    ingredient_role=ingredient_role,
                    prevalence=blended_prevalence,
                    source_count=source_count,
                    grams_per_100g=_mean(values) or 0.0,
                    classification="true_ingredient",
                )
                if (
                    candidate_score < 0.5
                    and support_prevalence < 0.02
                    and fndds_prevalence < 0.15
                    and ingredient_role not in _family_expected_roles(label)
                ):
                    rejected_reasons["low_support_fndds_candidate"] += 1
                    _insert_dish_variant_evidence_row(
                        connection,
                        label,
                        "fndds_input_food",
                        int(canonical_row.get("fdc_id") or 0) if canonical_row.get("fdc_id") is not None else None,
                        canonical_name or str(canonical_food_id),
                        candidate_score,
                        "ambiguous",
                        "low_support_fndds_candidate",
                    )
                    continue
                rows_to_insert.append(
                    {
                        "canonical_ingredient_id": canonical_food_id,
                        "mean_grams_per_100g_dish": _mean(values) or 0.0,
                        "median_grams_per_100g_dish": _median(values) or 0.0,
                        "mode_grams_per_100g_dish": _mode(values) or 0.0,
                        "prevalence": round(blended_prevalence, 6),
                        "recipe_count": max(1, source_count),
                        "source": "fndds_input_food",
                        "ingredient_role": ingredient_role,
                        "confidence_score": candidate_score,
                    }
                )
            rows_to_insert = _collapse_dish_prior_rows(rows_to_insert)
            for metric_key in (
                "mean_grams_per_100g_dish",
                "median_grams_per_100g_dish",
                "mode_grams_per_100g_dish",
            ):
                _normalize_dish_metric_rows(rows_to_insert, metric_key)
            for row in rows_to_insert:
                connection.execute(
                    """
                    INSERT INTO dish_ingredient_priors (
                        dish_key,
                        canonical_ingredient_id,
                        mean_grams_per_100g_dish,
                        median_grams_per_100g_dish,
                        mode_grams_per_100g_dish,
                        prevalence,
                        recipe_count,
                        source,
                        ingredient_role,
                        confidence_score,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        label,
                        row["canonical_ingredient_id"],
                        row["mean_grams_per_100g_dish"],
                        row["median_grams_per_100g_dish"],
                        row["mode_grams_per_100g_dish"],
                        row["prevalence"],
                        row["recipe_count"],
                        row["source"],
                        row.get("ingredient_role"),
                        row["confidence_score"],
                        _utc_now(),
                        _utc_now(),
                    ),
                )
                dish_prior_count += 1
            dish_label_count += 1
            continue

        fallback_rows: list[dict[str, Any]] = []
        recipe_supports = recipe_supports_by_label.get(label) or {}
        for support in recipe_supports.values():
            ingredient_candidates_scanned += 1
            classification = str(support.get("classification") or "ambiguous")
            reason = str(support.get("reason") or "unsupported_recipe_candidate")
            normalized = str(support.get("normalized") or "")
            ingredient_role = str(support.get("ingredient_role") or "other")
            if classification != "true_ingredient":
                _insert_dish_variant_evidence_row(
                    connection,
                    label,
                    "recipenlg_candidate_support",
                    None,
                    ", ".join(support.get("ingredient_texts") or [normalized]),
                    float(support.get("support_score") or 0.0),
                    classification,
                    reason,
                )
                composite_filtered_count += 1
                rejected_reasons[reason] += 1
                continue
            canonical_food_id = _ensure_alias_mapping(
                connection,
                catalog,
                fndds_frame,
                existing_fdc_map,
                normalized,
                "recipenlg_support",
                "dish_priors",
            )
            if canonical_food_id is None:
                rejected_reasons["recipe_support_no_canonical_mapping"] += 1
                _insert_dish_variant_evidence_row(
                    connection,
                    label,
                    "recipenlg_candidate_support",
                    None,
                    ", ".join(support.get("ingredient_texts") or [normalized]),
                    float(support.get("support_score") or 0.0),
                    "ambiguous",
                    "recipe_support_no_canonical_mapping",
                )
                continue
            grams = (
                float(support.get("mass_fraction") or 0.0) * 100.0
                if float(support.get("mass_fraction") or 0.0) > 0.0
                else float(support.get("frequency_weight") or 0.0) * 100.0
            )
            if grams <= 0.0:
                rejected_reasons["recipe_support_nonpositive_grams"] += 1
                continue
            score = float(support.get("support_score") or 0.0)
            prevalence = float(support.get("prevalence") or 0.0)
            source_count = int(support.get("source_count") or 0)
            if (
                score < 0.52
                and prevalence < 0.015
                and source_count < 8
                and ingredient_role not in _family_expected_roles(label)
            ):
                rejected_reasons["low_support_recipe_candidate"] += 1
                _insert_dish_variant_evidence_row(
                    connection,
                    label,
                    "recipenlg_candidate_support",
                    None,
                    ", ".join(support.get("ingredient_texts") or [normalized]),
                    score,
                    "ambiguous",
                    "low_support_recipe_candidate",
                )
                continue
            fallback_rows.append(
                {
                    "canonical_ingredient_id": canonical_food_id,
                    "mean_grams_per_100g_dish": grams,
                    "median_grams_per_100g_dish": grams,
                    "mode_grams_per_100g_dish": grams,
                    "prevalence": round(prevalence, 6),
                    "recipe_count": max(1, source_count),
                    "source": (
                        "recipenlg_mass_prior_fallback"
                        if "recipenlg_mass" in (support.get("sources") or [])
                        else "recipenlg_frequency_prior_fallback"
                    ),
                    "ingredient_role": ingredient_role,
                    "confidence_score": score,
                }
            )
            true_ingredients_kept += 1
        if not fallback_rows:
            continue
        fallback_rows = _collapse_dish_prior_rows(fallback_rows)
        for metric_key in (
            "mean_grams_per_100g_dish",
            "median_grams_per_100g_dish",
            "mode_grams_per_100g_dish",
        ):
            _normalize_dish_metric_rows(fallback_rows, metric_key)
        _insert_dish_label_row(
            connection,
            label,
            display_name,
            connection.execute(
                "SELECT canonical_food_id FROM dish_labels WHERE dish_key = ?",
                (label,),
            ).fetchone()["canonical_food_id"],
            fallback_rows[0]["source"],
            fallback_rows[0]["confidence_score"],
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO dish_aliases (alias_name, dish_key, source, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (display_name.lower(), label, "food101_display", _utc_now()),
        )
        for row in fallback_rows:
            connection.execute(
                """
                INSERT INTO dish_ingredient_priors (
                    dish_key,
                    canonical_ingredient_id,
                    mean_grams_per_100g_dish,
                    median_grams_per_100g_dish,
                    mode_grams_per_100g_dish,
                    prevalence,
                    recipe_count,
                    source,
                    ingredient_role,
                    confidence_score,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    label,
                    row["canonical_ingredient_id"],
                    row["mean_grams_per_100g_dish"],
                    row["median_grams_per_100g_dish"],
                    row["mode_grams_per_100g_dish"],
                    row["prevalence"],
                    row["recipe_count"],
                    row["source"],
                    row.get("ingredient_role"),
                    row["confidence_score"],
                    _utc_now(),
                    _utc_now(),
                ),
            )
            dish_prior_count += 1
        dish_label_count += 1

    return {
        "dish_labels": dish_label_count,
        "dish_aliases": int(connection.execute("SELECT COUNT(*) FROM dish_aliases").fetchone()[0]),
        "dish_ingredient_priors": dish_prior_count,
        "dish_variant_evidence": int(connection.execute("SELECT COUNT(*) FROM dish_variant_evidence").fetchone()[0]),
        "composite_rows_filtered_from_ingredient_priors": composite_filtered_count,
        "ingredient_candidates_scanned": ingredient_candidates_scanned,
        "true_ingredients_kept": true_ingredients_kept,
        "rejected_candidate_reasons": dict(sorted(rejected_reasons.items())),
    }


def build_palm_v2_db(
    output_db_path: str | Path = PALM_V2_DB_PATH,
    usda_data_dir: str | Path = USDA_DATA_DIR,
) -> dict[str, Any]:
    output_path = Path(output_db_path)
    data_dir = Path(usda_data_dir)
    for cleanup_path in (
        output_path,
        output_path.with_name(f"{output_path.name}-wal"),
        output_path.with_name(f"{output_path.name}-shm"),
    ):
        if cleanup_path.exists():
            cleanup_path.unlink()
    with closing(_create_connection(output_path)) as connection:
        import_summary = import_required_usda_files(connection, data_dir)
        _create_runtime_schema(connection)
        nutrient_map = _load_nutrient_map(connection)
        direct_rows = _build_direct_source_rows(connection, nutrient_map)
        existing_fdc_map = _insert_direct_canonical_rows(connection, direct_rows)
        fndds_rows = _build_fndds_candidate_rows(connection, nutrient_map)
        catalog = _load_catalog_frame(connection)
        fndds_frame = _build_fndds_frame(fndds_rows, existing_fdc_map)
        alias_summary = _seed_aliases(connection, catalog, fndds_frame, existing_fdc_map)
        catalog = _load_catalog_frame(connection)
        portion_count = _build_portion_priors(connection)
        product_summary = _build_products(connection, nutrient_map)
        dish_summary = _build_dish_priors(connection, catalog, fndds_frame, existing_fdc_map)
        _create_runtime_indexes(connection)
        connection.commit()

        table_counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0])
            for table in (
                "canonical_foods",
                "canonical_food_aliases",
                "canonical_food_nutrients",
                "food_portion_priors",
                "dish_labels",
                "dish_aliases",
                "dish_variant_evidence",
                "dish_ingredient_priors",
                "nutrition_sources",
                "products",
                "barcodes",
                "serving_sizes",
            )
        }

    return {
        "db_path": str(output_path),
        "raw_imports": import_summary,
        "canonical_foods_built": table_counts["canonical_foods"],
        "canonical_food_aliases_built": table_counts["canonical_food_aliases"],
        "canonical_nutrients_built": table_counts["canonical_food_nutrients"],
        "portion_priors_built": portion_count,
        "dish_labels_built": dish_summary["dish_labels"],
        "dish_aliases_built": dish_summary["dish_aliases"],
        "dish_variant_evidence_built": dish_summary["dish_variant_evidence"],
        "dish_ingredient_priors_built": dish_summary["dish_ingredient_priors"],
        "composite_rows_filtered_from_ingredient_priors": dish_summary["composite_rows_filtered_from_ingredient_priors"],
        "ingredient_candidates_scanned": dish_summary["ingredient_candidates_scanned"],
        "true_ingredients_kept": dish_summary["true_ingredients_kept"],
        "rejected_candidate_reasons": dish_summary["rejected_candidate_reasons"],
        "nutrition_sources_built": table_counts["nutrition_sources"],
        "product_rows_built": product_summary["products"],
        "barcode_rows_built": product_summary["barcodes"],
        "serving_sizes_built": product_summary["serving_sizes"],
        "alias_summary": alias_summary,
    }


def _print_build_summary(summary: dict[str, Any], validation: dict[str, Any] | None = None) -> None:
    print("PALM V2 Build Summary")
    print("=" * 22)
    print("Raw imports:")
    for raw_table, payload in summary["raw_imports"].items():
        print(f"  {raw_table}: {payload['row_count']}")
    print(f"Canonical foods built: {summary['canonical_foods_built']}")
    print(f"Canonical nutrients built: {summary['canonical_nutrients_built']}")
    print(f"Portion priors built: {summary['portion_priors_built']}")
    print(f"Dish labels built: {summary['dish_labels_built']}")
    print(f"Dish evidence rows built: {summary['dish_variant_evidence_built']}")
    print(f"Dish ingredient priors built: {summary['dish_ingredient_priors_built']}")
    print(f"Ingredient candidates scanned: {summary['ingredient_candidates_scanned']}")
    print(f"True ingredients kept: {summary['true_ingredients_kept']}")
    print(
        "Composite rows filtered from ingredient priors: "
        f"{summary['composite_rows_filtered_from_ingredient_priors']}"
    )
    if summary.get("rejected_candidate_reasons"):
        print("Rejected candidates by reason:")
        for reason, count in summary["rejected_candidate_reasons"].items():
            print(f"  {reason}: {count}")
    print(f"Products built: {summary['product_rows_built']}")
    print(f"Barcodes built: {summary['barcode_rows_built']}")
    if validation is not None:
        print(
            "Validation: "
            + ("PASS" if validation.get("passed") else "FAIL")
            + f" ({len(validation.get('errors') or [])} errors)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the compact PALM V2 nutrition database.")
    parser.add_argument("--output-db", type=Path, default=PALM_V2_DB_PATH)
    parser.add_argument("--usda-data-dir", type=Path, default=USDA_DATA_DIR)
    parser.add_argument("--skip-validation", action="store_true")
    args = parser.parse_args()

    summary = build_palm_v2_db(args.output_db, args.usda_data_dir)
    validation: dict[str, Any] | None = None
    if not args.skip_validation:
        from food_ai.validation.validate_palm_v2_db import validate_palm_v2_db

        validation = validate_palm_v2_db(args.output_db, args.usda_data_dir)
    _print_build_summary(summary, validation)
    if validation and not validation.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
