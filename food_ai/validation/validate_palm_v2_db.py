"""Validate PALM_V2 ingredient and nutrition behavior across representative dishes."""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from food_ai.config import PALM_V2_DB_PATH, USDA_DATA_DIR
from food_ai.data.builders.build_palm_v2_db import (
    COMPOSITE_EVIDENCE_PATTERNS,
    FOUNDATION_PRIORITY_RULE,
    RAW_SOURCE_FILES,
    TARGET_NUTRIENTS,
    USER_FACING_INGREDIENT_ALLOWLIST,
    _dish_family,
    _family_expected_roles,
)


REQUIRED_RAW_TABLES = tuple(raw_table for _, raw_table in RAW_SOURCE_FILES)
REQUIRED_RUNTIME_TABLES = (
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
    "consumption_logs",
    "meal_log_ingredients",
    "unknown_foods",
    "unknown_barcodes",
    "user_food_corrections",
)
REQUIRED_NUTRIENT_COLUMNS = tuple(TARGET_NUTRIENTS.keys()) + (
    "source_fdc_id",
    "nutrient_coverage_score",
    "derivation_quality_score",
)
PLURAL_DUPLICATE_RAW_TABLES = ("usda_food_portions", "usda_measure_units")
SAMPLE_FOODS = (
    "cooked spaghetti",
    "dry spaghetti",
    "lasagna noodle",
    "ricotta cheese",
    "mozzarella cheese",
    "tomato sauce",
    "ground beef",
    "glazed donut",
    "lasagna",
)
DISH_VALIDATION_PATTERNS = {
    "lasagna": "lasagna",
    "spaghetti": "spaghetti",
    "glazed_donut": "donut",
    "pizza": "pizza",
    "hamburger": "hamburger",
    "salad": "salad",
    "fried_rice": "fried rice",
    "pancakes": "pancake",
    "omelet": "omelet",
    "macaroni_and_cheese": "macaroni and cheese",
    "taco": "taco",
    "burrito": "burrito",
    "chicken_curry": "curry",
    "grilled_cheese_sandwich": "grilled cheese",
    "chocolate_cake": "cake",
}
FORM_CONSTRAINT_ALIASES = {
    "egg": ("dried", "powder"),
    "milk": ("dry", "powder"),
    "pasta": (),
}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _append_error(errors: list[str], condition: bool, message: str) -> None:
    if condition:
        errors.append(message)


def _append_warning(warnings: list[str], condition: bool, message: str) -> None:
    if condition:
        warnings.append(message)


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_text = " " + " ".join(str(text or "").strip().lower().split()) + " "
    normalized_phrase = " ".join(str(phrase or "").strip().lower().split())
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in normalized_text


def _is_banned_user_facing_ingredient(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if lowered in {" ".join(value.strip().lower().split()) for value in USER_FACING_INGREDIENT_ALLOWLIST}:
        return False
    return any(_contains_phrase(lowered, phrase) for phrase in COMPOSITE_EVIDENCE_PATTERNS)


def _sample_food_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample_name in SAMPLE_FOODS:
        row = connection.execute(
            """
            SELECT
                a.alias_name AS sample_name,
                cf.canonical_name,
                cf.prep_state,
                cf.preferred_fdc_id,
                cf.preferred_source,
                n.calories_per_100g,
                n.protein_g_per_100g,
                n.fat_g_per_100g,
                n.carbs_g_per_100g,
                n.nutrient_coverage_score
            FROM canonical_food_aliases a
            INNER JOIN canonical_foods cf ON cf.canonical_food_id = a.canonical_food_id
            INNER JOIN canonical_food_nutrients n ON n.canonical_food_id = cf.canonical_food_id
            WHERE a.alias_name = ?
            """,
            (sample_name,),
        ).fetchone()
        rows.append(dict(row) if row is not None else {"sample_name": sample_name, "missing": True})
    return rows


def _find_sample_dish_key(connection: sqlite3.Connection, pattern: str) -> str | None:
    row = connection.execute(
        """
        SELECT dish_key
        FROM dish_labels
        WHERE LOWER(dish_key) LIKE LOWER(?)
           OR LOWER(display_name) LIKE LOWER(?)
           OR LOWER(COALESCE(dish_label, '')) LIKE LOWER(?)
        ORDER BY CASE WHEN LOWER(dish_key) = LOWER(?) THEN 0 ELSE 1 END, dish_key
        LIMIT 1
        """,
        (f"%{pattern}%", f"%{pattern}%", f"%{pattern}%", pattern),
    ).fetchone()
    return str(row["dish_key"]) if row is not None else None


def _dish_prior_rows(connection: sqlite3.Connection, dish_key: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            d.dish_key,
            cf.canonical_name,
            cf.prep_state,
            cf.preferred_fdc_id,
            cf.preferred_source,
            d.mean_grams_per_100g_dish,
            d.median_grams_per_100g_dish,
            d.mode_grams_per_100g_dish,
            d.prevalence,
            d.recipe_count,
            d.source,
            d.ingredient_role,
            d.confidence_score,
            ROUND(COALESCE(d.mean_grams_per_100g_dish, 0) * COALESCE(n.calories_per_100g, 0) / 100.0, 3) AS calories_contribution,
            ROUND(COALESCE(d.mean_grams_per_100g_dish, 0) * COALESCE(n.protein_g_per_100g, 0) / 100.0, 3) AS protein_contribution,
            ROUND(COALESCE(d.mean_grams_per_100g_dish, 0) * COALESCE(n.fat_g_per_100g, 0) / 100.0, 3) AS fat_contribution,
            ROUND(COALESCE(d.mean_grams_per_100g_dish, 0) * COALESCE(n.carbs_g_per_100g, 0) / 100.0, 3) AS carbs_contribution
        FROM dish_ingredient_priors d
        INNER JOIN canonical_foods cf ON cf.canonical_food_id = d.canonical_ingredient_id
        INNER JOIN canonical_food_nutrients n ON n.canonical_food_id = d.canonical_ingredient_id
        WHERE d.dish_key = ?
        ORDER BY d.mean_grams_per_100g_dish DESC, cf.canonical_name
        LIMIT ?
        """,
        (dish_key, max(1, int(limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def _dish_evidence_rows(connection: sqlite3.Connection, dish_key: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT source, source_fdc_id, source_description, normalized_description, match_score, evidence_type, reason
        FROM dish_variant_evidence
        WHERE dish_key = ?
        ORDER BY COALESCE(match_score, 0.0) DESC, source_description
        LIMIT ?
        """,
        (dish_key, max(1, int(limit))),
    ).fetchall()
    return [dict(row) for row in rows]


def _dish_samples(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for label, pattern in DISH_VALIDATION_PATTERNS.items():
        dish_key = _find_sample_dish_key(connection, pattern)
        result[label] = {
            "dish_key": dish_key,
            "ingredients": _dish_prior_rows(connection, dish_key, limit=200) if dish_key else [],
            "evidence": _dish_evidence_rows(connection, dish_key, limit=20) if dish_key else [],
        }
    return result


def _barcode_latest_publication_mismatches(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        WITH latest_raw AS (
            SELECT
                barcode,
                source_fdc_id AS expected_fdc_id,
                publication_date AS expected_publication_date
            FROM (
                SELECT
                    b.gtin_upc AS barcode,
                    CAST(b.fdc_id AS INTEGER) AS source_fdc_id,
                    f.publication_date,
                    ROW_NUMBER() OVER (
                        PARTITION BY b.gtin_upc
                        ORDER BY f.publication_date DESC, CAST(b.fdc_id AS INTEGER) DESC
                    ) AS row_num
                FROM usda_branded_food b
                INNER JOIN usda_food f ON f.fdc_id = b.fdc_id
                WHERE COALESCE(TRIM(b.gtin_upc), '') <> ''
            )
            WHERE row_num = 1
        )
        SELECT
            bc.barcode,
            bc.source_fdc_id AS selected_fdc_id,
            bc.publication_date AS selected_publication_date,
            lr.expected_fdc_id,
            lr.expected_publication_date
        FROM barcodes bc
        INNER JOIN latest_raw lr ON lr.barcode = bc.barcode
        WHERE COALESCE(CAST(bc.source_fdc_id AS INTEGER), -1) <> COALESCE(lr.expected_fdc_id, -1)
           OR COALESCE(bc.publication_date, '') <> COALESCE(lr.expected_publication_date, '')
        ORDER BY bc.barcode
        LIMIT 25
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _foundation_membership_audit(connection: sqlite3.Connection) -> dict[str, Any]:
    count_row = connection.execute(
        """
        SELECT COUNT(*) AS missing_count
        FROM usda_food f
        LEFT JOIN usda_foundation_food ff ON ff.fdc_id = f.fdc_id
        WHERE LOWER(COALESCE(f.data_type, '')) = 'foundation_food'
          AND ff.fdc_id IS NULL
        """
    ).fetchone()
    examples = connection.execute(
        """
        SELECT f.fdc_id, f.description, f.publication_date
        FROM usda_food f
        LEFT JOIN usda_foundation_food ff ON ff.fdc_id = f.fdc_id
        WHERE LOWER(COALESCE(f.data_type, '')) = 'foundation_food'
          AND ff.fdc_id IS NULL
        ORDER BY CAST(f.fdc_id AS INTEGER)
        LIMIT 10
        """
    ).fetchall()
    return {
        "priority_rule": FOUNDATION_PRIORITY_RULE,
        "missing_count": int(count_row["missing_count"]),
        "examples": [dict(row) for row in examples],
    }


def _pasta_prep_sanity_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            cf.canonical_name,
            cf.prep_state,
            cf.preferred_fdc_id,
            cf.preferred_source,
            ns.usda_description AS source_description,
            n.calories_per_100g,
            n.carbs_g_per_100g
        FROM canonical_foods cf
        INNER JOIN canonical_food_nutrients n ON n.canonical_food_id = cf.canonical_food_id
        LEFT JOIN nutrition_sources ns ON ns.source_fdc_id = cf.preferred_fdc_id
        WHERE cf.food_role = 'ingredient'
          AND (
               LOWER(COALESCE(cf.canonical_name, '')) LIKE '%pasta%'
            OR LOWER(COALESCE(cf.canonical_name, '')) LIKE '%spaghetti%'
            OR LOWER(COALESCE(cf.canonical_name, '')) LIKE '%lasagna%'
            OR LOWER(COALESCE(cf.canonical_name, '')) LIKE '%noodle%'
            OR LOWER(COALESCE(ns.usda_description, '')) LIKE '%pasta%'
            OR LOWER(COALESCE(ns.usda_description, '')) LIKE '%spaghetti%'
            OR LOWER(COALESCE(ns.usda_description, '')) LIKE '%lasagna%'
            OR LOWER(COALESCE(ns.usda_description, '')) LIKE '%noodle%'
          )
        ORDER BY cf.prep_state, n.calories_per_100g DESC, cf.canonical_name
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _dish_prior_composite_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            d.dish_key,
            cf.canonical_name,
            cf.prep_state,
            d.mean_grams_per_100g_dish,
            d.source
        FROM dish_ingredient_priors d
        INNER JOIN canonical_foods cf ON cf.canonical_food_id = d.canonical_ingredient_id
        ORDER BY d.dish_key, d.mean_grams_per_100g_dish DESC, cf.canonical_name
        """
    ).fetchall()
    return [dict(row) for row in rows if _is_banned_user_facing_ingredient(str(row["canonical_name"]))][:50]


def _plural_duplicate_raw_tables(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in PLURAL_DUPLICATE_RAW_TABLES:
        if not _table_exists(connection, table):
            continue
        rows.append({"table": table, "row_count": int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])})
    return rows


def _alias_mapping_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            a.alias_name,
            cf.canonical_name,
            cf.prep_state,
            cf.preferred_source,
            ns.usda_description
        FROM canonical_food_aliases a
        INNER JOIN canonical_foods cf ON cf.canonical_food_id = a.canonical_food_id
        LEFT JOIN nutrition_sources ns ON ns.source_fdc_id = cf.preferred_fdc_id
        WHERE a.alias_name IN ('egg', 'milk', 'flour', 'sugar', 'glaze', 'oil', 'olive oil', 'onion', 'mushroom', 'green pepper', 'bun')
        ORDER BY a.alias_name
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _validate_form_constraints(errors: list[str], alias_rows: list[dict[str, Any]]) -> None:
    by_alias = {str(row["alias_name"]): row for row in alias_rows}
    egg_row = by_alias.get("egg")
    if egg_row and any(token in str(egg_row["usda_description"]).lower() for token in FORM_CONSTRAINT_ALIASES["egg"]):
        errors.append(f"Plain egg alias mapped to incompatible dry form: {egg_row['usda_description']}")
    milk_row = by_alias.get("milk")
    if milk_row and any(token in str(milk_row["usda_description"]).lower() for token in FORM_CONSTRAINT_ALIASES["milk"]):
        errors.append(f"Plain milk alias mapped to incompatible dry form: {milk_row['usda_description']}")
    sugar_row = by_alias.get("sugar")
    if sugar_row and "granulated" not in str(sugar_row["usda_description"]).lower():
        errors.append(f"Plain sugar alias mapped to unexpected source: {sugar_row['usda_description']}")
    onion_row = by_alias.get("onion")
    if onion_row and "powder" in str(onion_row["usda_description"]).lower():
        errors.append(f"Plain onion alias mapped to powder form: {onion_row['usda_description']}")
    mushroom_row = by_alias.get("mushroom")
    if mushroom_row and any(token in str(mushroom_row["usda_description"]).lower() for token in ("gravy", "powder")):
        errors.append(f"Plain mushroom alias mapped to incompatible form: {mushroom_row['usda_description']}")
    green_pepper_row = by_alias.get("green pepper")
    if green_pepper_row and "pepper" not in str(green_pepper_row["usda_description"]).lower():
        errors.append(f"Green pepper alias mapped to unexpected source: {green_pepper_row['usda_description']}")


def _dish_family_expectation_groups(label: str) -> dict[str, tuple[str, ...]]:
    groups = {
        "lasagna": {
            "starch": ("pasta", "noodle"),
            "sauce": ("sauce", "tomato"),
            "dairy": ("mozzarella", "ricotta", "parmesan", "cheese"),
            "protein": ("beef", "meat", "turkey", "chicken"),
        },
        "spaghetti": {
            "starch": ("spaghetti", "pasta", "noodle"),
            "sauce": ("sauce", "tomato", "marinara"),
        },
        "glazed_donut": {
            "base": ("flour",),
            "sweetener": ("sugar", "glaze", "icing"),
            "fat": ("oil", "shortening", "butter"),
            "binder": ("egg", "milk", "buttermilk"),
        },
        "pizza": {
            "base": ("bread", "bun", "crust", "dough", "flour"),
            "sauce": ("sauce", "tomato"),
            "dairy": ("mozzarella", "cheese"),
        },
        "fried_rice": {
            "base": ("rice",),
            "protein": ("egg", "chicken", "beef", "pork", "shrimp", "tofu"),
            "vegetable": ("onion", "scallion", "carrot", "pea", "garlic"),
        },
        "macaroni_and_cheese": {
            "base": ("macaroni", "pasta", "noodle"),
            "dairy": ("cheese", "milk", "butter"),
        },
    }
    return groups.get(label, {})


def _dish_validation_verdict(label: str, rows: list[dict[str, Any]]) -> tuple[str, list[str]]:
    issues: list[str] = []
    if not rows:
        return "FAIL", ["missing_dish_priors"]
    total_grams = sum(float(row.get("mean_grams_per_100g_dish") or 0.0) for row in rows)
    if total_grams < 98.0 or total_grams > 102.0:
        issues.append("grams_not_near_100")
    top_names = [str(row.get("canonical_name") or "").lower() for row in rows[:12]]
    if any(_is_banned_user_facing_ingredient(name) for name in top_names):
        issues.append("top_rows_contain_composite")
    expected_groups = _dish_family_expectation_groups(label)
    if expected_groups:
        matched_groups = {
            group_name
            for group_name, tokens in expected_groups.items()
            if any(any(token in name for token in tokens) for name in top_names)
        }
        minimum = max(1, min(3, len(expected_groups)))
        if len(matched_groups) < minimum:
            issues.append(f"missing_expected_groups:{','.join(sorted(matched_groups))}")
    expected_roles = _family_expected_roles(label)
    weird_top = [
        row
        for row in rows[:8]
        if float(row.get("prevalence") or 0.0) < 0.02
        and int(row.get("recipe_count") or 0) < 8
        and str(row.get("ingredient_role") or "other") not in expected_roles
    ]
    if weird_top:
        issues.append("low_support_weird_top_ingredients")
    if any(issue in {"grams_not_near_100", "top_rows_contain_composite", "missing_dish_priors"} for issue in issues):
        return "FAIL", issues
    if issues:
        return "WARN", issues
    return "PASS", []


def validate_palm_v2_db(
    db_path: str | Path = PALM_V2_DB_PATH,
    usda_data_dir: str | Path = USDA_DATA_DIR,
) -> dict[str, Any]:
    database_path = Path(db_path)
    data_dir = Path(usda_data_dir)
    errors: list[str] = []
    warnings: list[str] = []

    for filename, _ in RAW_SOURCE_FILES:
        _append_error(errors, not (data_dir / filename).exists(), f"Missing required raw source file: {data_dir / filename}")
    _append_error(errors, not database_path.exists(), f"Database not found: {database_path}")
    if errors:
        return {"passed": False, "errors": errors, "warnings": warnings, "sample_rows": []}

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        for table in REQUIRED_RAW_TABLES:
            _append_error(errors, not _table_exists(connection, table), f"Missing required raw table: {table}")
        for table in REQUIRED_RUNTIME_TABLES:
            _append_error(errors, not _table_exists(connection, table), f"Missing required runtime table: {table}")
        if not errors:
            nutrient_columns = _table_columns(connection, "canonical_food_nutrients")
            for column in REQUIRED_NUTRIENT_COLUMNS:
                _append_error(errors, column not in nutrient_columns, f"Missing canonical_food_nutrients column: {column}")
        plural_duplicate_rows = _plural_duplicate_raw_tables(connection)
        if plural_duplicate_rows:
            errors.append("Duplicate plural raw tables still exist: " + ", ".join(f"{row['table']}({row['row_count']})" for row in plural_duplicate_rows))
        dish_label_columns = _table_columns(connection, "dish_labels") if _table_exists(connection, "dish_labels") else set()
        _append_error(errors, "display_name" not in dish_label_columns, "dish_labels is missing display_name")
        _append_error(errors, "dish_label" not in dish_label_columns, "dish_labels is missing compatibility alias column dish_label")
        _append_error(errors, "ingredient_role" not in _table_columns(connection, "dish_ingredient_priors"), "dish_ingredient_priors is missing ingredient_role")
        _append_error(errors, "normalized_description" not in _table_columns(connection, "dish_variant_evidence"), "dish_variant_evidence is missing normalized_description")
        _append_error(errors, "reason" not in _table_columns(connection, "dish_variant_evidence"), "dish_variant_evidence is missing reason")
        if errors:
            return {"passed": False, "errors": errors, "warnings": warnings, "sample_rows": []}

        duplicate_aliases = connection.execute(
            "SELECT COUNT(*) AS duplicate_count FROM (SELECT alias_name FROM canonical_food_aliases GROUP BY alias_name HAVING COUNT(*) > 1)"
        ).fetchone()["duplicate_count"]
        _append_error(errors, duplicate_aliases > 0, "Duplicate alias_name rows found in canonical_food_aliases")
        null_fdc_ids = connection.execute(
            """
            SELECT COUNT(*) AS missing_count
            FROM canonical_foods
            WHERE preferred_source IN ('foundation_food', 'sr_legacy_food', 'survey_fndds_food', 'fndds_ingredient_nutrient_value')
              AND preferred_fdc_id IS NULL
            """
        ).fetchone()["missing_count"]
        _append_error(errors, null_fdc_ids > 0, "USDA-derived canonical_foods rows contain null preferred_fdc_id")
        negative_values = connection.execute(
            """
            SELECT COUNT(*) AS invalid_count
            FROM canonical_food_nutrients
            WHERE calories_per_100g < 0
               OR protein_g_per_100g < 0
               OR fat_g_per_100g < 0
               OR saturated_fat_g_per_100g < 0
               OR carbs_g_per_100g < 0
               OR fiber_g_per_100g < 0
               OR sugar_g_per_100g < 0
               OR sodium_mg_per_100g < 0
               OR cholesterol_mg_per_100g < 0
               OR calcium_mg_per_100g < 0
               OR iron_mg_per_100g < 0
               OR potassium_mg_per_100g < 0
            """
        ).fetchone()["invalid_count"]
        _append_error(errors, negative_values > 0, "Negative nutrient values detected in canonical_food_nutrients")
        impossible_macros = connection.execute(
            """
            SELECT COUNT(*) AS invalid_count
            FROM canonical_food_nutrients
            WHERE protein_g_per_100g > 100
               OR fat_g_per_100g > 100
               OR carbs_g_per_100g > 100
               OR saturated_fat_g_per_100g > fat_g_per_100g + 1.0
               OR fiber_g_per_100g > carbs_g_per_100g + 1.0
               OR sugar_g_per_100g > carbs_g_per_100g + 1.0
               OR calories_per_100g > 902.5
            """
        ).fetchone()["invalid_count"]
        _append_error(errors, impossible_macros > 0, "Impossible macro rows detected in canonical_food_nutrients")
        missing_core_macros = connection.execute(
            """
            SELECT COUNT(*) AS missing_count
            FROM canonical_foods cf
            INNER JOIN canonical_food_nutrients n ON n.canonical_food_id = cf.canonical_food_id
            WHERE cf.preferred_source IN ('foundation_food', 'sr_legacy_food', 'survey_fndds_food', 'fndds_ingredient_nutrient_value')
              AND (n.calories_per_100g IS NULL OR n.protein_g_per_100g IS NULL OR n.fat_g_per_100g IS NULL OR n.carbs_g_per_100g IS NULL)
            """
        ).fetchone()["missing_count"]
        _append_error(errors, missing_core_macros > 0, "USDA-derived canonical rows are missing calories/protein/fat/carbs")
        bad_portions = connection.execute("SELECT COUNT(*) AS invalid_count FROM food_portion_priors WHERE gram_weight <= 0").fetchone()["invalid_count"]
        _append_error(errors, bad_portions > 0, "food_portion_priors contains nonpositive gram_weight rows")
        bad_dish_sums = connection.execute(
            """
            SELECT COUNT(*) AS invalid_count
            FROM (
                SELECT dish_key, SUM(mean_grams_per_100g_dish) AS grams_total
                FROM dish_ingredient_priors
                GROUP BY dish_key
                HAVING grams_total < 98 OR grams_total > 102
            )
            """
        ).fetchone()["invalid_count"]
        _append_error(errors, bad_dish_sums > 0, "dish_ingredient_priors mean grams do not sum near 100g for one or more dishes")

        sample_rows = _sample_food_rows(connection)
        for row in sample_rows:
            if row.get("missing"):
                errors.append(f"Missing sample food mapping: {row['sample_name']}")

        alias_mapping_rows = _alias_mapping_rows(connection)
        _validate_form_constraints(errors, alias_mapping_rows)

        pasta_prep_rows = _pasta_prep_sanity_rows(connection)
        pasta_prep_failures = [
            row
            for row in pasta_prep_rows
            if (str(row["prep_state"]).lower() == "cooked" and float(row["calories_per_100g"] or 0.0) > 250.0)
            or (str(row["prep_state"]).lower() in {"dry", "unprepared"} and float(row["calories_per_100g"] or 0.0) < 250.0)
        ]
        if pasta_prep_failures:
            errors.extend(
                [
                    "Pasta/noodle prep-state sanity failure: "
                    f"{row['canonical_name']} | prep={row['prep_state']} | fdc_id={row['preferred_fdc_id']} | "
                    f"kcal={row['calories_per_100g']} | carbs={row['carbs_g_per_100g']} | desc={row['source_description']}"
                    for row in pasta_prep_failures[:25]
                ]
            )

        composite_dish_prior_rows = _dish_prior_composite_rows(connection)
        _append_error(errors, bool(composite_dish_prior_rows), "dish_ingredient_priors contains banned composite user-facing ingredient rows")

        dish_samples = _dish_samples(connection)
        dish_verdicts: dict[str, dict[str, Any]] = {}
        for label, payload in dish_samples.items():
            verdict, issues = _dish_validation_verdict(label, payload.get("ingredients") or [])
            dish_verdicts[label] = {"verdict": verdict, "issues": issues, "dish_key": payload.get("dish_key")}
            if verdict == "FAIL":
                errors.append(f"{label} ingredient logic failed: {', '.join(issues)}")
            elif verdict == "WARN":
                warnings.append(f"{label} ingredient logic warning: {', '.join(issues)}")

        barcode_mismatches = _barcode_latest_publication_mismatches(connection)
        if barcode_mismatches:
            errors.extend(
                [
                    "Barcode latest-publication mismatch: "
                    f"barcode={row['barcode']} selected_fdc_id={row['selected_fdc_id']} selected_date={row['selected_publication_date']} "
                    f"expected_fdc_id={row['expected_fdc_id']} expected_date={row['expected_publication_date']}"
                    for row in barcode_mismatches
                ]
            )
        foundation_audit = _foundation_membership_audit(connection)

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "sample_rows": sample_rows,
        "dish_samples": dish_samples,
        "dish_verdicts": dish_verdicts,
        "pasta_prep_rows": pasta_prep_rows[:25],
        "pasta_prep_failures": pasta_prep_failures[:25],
        "barcode_latest_publication_mismatches": barcode_mismatches,
        "foundation_membership_audit": foundation_audit,
        "plural_duplicate_raw_tables": plural_duplicate_rows,
        "dish_prior_composite_rows": composite_dish_prior_rows,
        "alias_mapping_rows": alias_mapping_rows,
    }


def _print_validation_report(report: dict[str, Any]) -> None:
    print("PALM V2 Validation")
    print("=" * 18)
    print("PASS" if report["passed"] else "FAIL")
    print(f"Foundation priority rule: {report.get('foundation_membership_audit', {}).get('priority_rule')}")
    if report.get("errors"):
        print("Errors:")
        for error in report["errors"]:
            print(f"  - {error}")
    if report.get("warnings"):
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")

    print("Sample foods:")
    for row in report["sample_rows"]:
        if row.get("missing"):
            print(f"  {row['sample_name']}: MISSING")
            continue
        print(
            "  {sample_name}: canonical={canonical_name} | prep={prep_state} | fdc_id={preferred_fdc_id} | "
            "source={preferred_source} | kcal={calories_per_100g} | protein={protein_g_per_100g} | "
            "fat={fat_g_per_100g} | carbs={carbs_g_per_100g} | coverage={nutrient_coverage_score}".format(**row)
        )

    print("Dish verdicts:")
    for label, payload in (report.get("dish_verdicts") or {}).items():
        print(f"  {label}: {payload.get('verdict')} | issues={payload.get('issues') or []}")

    print("Representative ingredient tables:")
    for label in ("lasagna", "glazed_donut", "pizza", "fried_rice"):
        payload = (report.get("dish_samples") or {}).get(label) or {}
        rows = payload.get("ingredients") or []
        evidence = payload.get("evidence") or []
        print(f"  {label}:")
        if not rows:
            print("    MISSING")
            continue
        for row in rows[:12]:
            print(
                "    {canonical_name} | grams100={mean_grams_per_100g_dish} | role={ingredient_role} | "
                "source_type={preferred_source} | confidence={confidence_score} | kcal={calories_contribution} | "
                "protein={protein_contribution} | fat={fat_contribution} | carbs={carbs_contribution}".format(**row)
            )
        print("    evidence:")
        for row in evidence[:8]:
            print(
                "      {evidence_type} | {source_description} | reason={reason} | score={match_score}".format(**row)
            )

    print("Alias form checks:")
    for row in report.get("alias_mapping_rows") or []:
        print(
            "  {alias_name} -> {canonical_name} | prep={prep_state} | source={preferred_source} | desc={usda_description}".format(**row)
        )

    print("Pasta prep-state sanity rows:")
    for row in report.get("pasta_prep_rows") or []:
        print(
            "  {canonical_name} | prep={prep_state} | fdc_id={preferred_fdc_id} | "
            "kcal={calories_per_100g} | carbs={carbs_g_per_100g} | desc={source_description}".format(**row)
        )

    print("Barcode latest-publication validation:")
    mismatches = report.get("barcode_latest_publication_mismatches") or []
    if not mismatches:
        print("  OK")
    else:
        for row in mismatches:
            print(
                "  barcode={barcode} | selected_fdc_id={selected_fdc_id} | selected_date={selected_publication_date} | "
                "expected_fdc_id={expected_fdc_id} | expected_date={expected_publication_date}".format(**row)
            )

    print("Foundation membership validation:")
    foundation_audit = report.get("foundation_membership_audit") or {}
    print(f"  missing_count={foundation_audit.get('missing_count')}")
    for row in foundation_audit.get("examples") or []:
        print("  missing foundation member: fdc_id={fdc_id} | desc={description} | publication_date={publication_date}".format(**row))

    if report.get("plural_duplicate_raw_tables"):
        print("Duplicate plural raw tables:")
        for row in report["plural_duplicate_raw_tables"]:
            print(f"  {row['table']}: {row['row_count']}")
    if report.get("dish_prior_composite_rows"):
        print("Composite rows still present in dish_ingredient_priors:")
        for row in report["dish_prior_composite_rows"][:20]:
            print("  {dish_key} | {canonical_name} | prep={prep_state} | grams100={mean_grams_per_100g_dish} | source={source}".format(**row))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the compact PALM V2 nutrition database.")
    parser.add_argument("--db-path", type=Path, default=PALM_V2_DB_PATH)
    parser.add_argument("--usda-data-dir", type=Path, default=USDA_DATA_DIR)
    args = parser.parse_args()
    report = validate_palm_v2_db(args.db_path, args.usda_data_dir)
    _print_validation_report(report)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
