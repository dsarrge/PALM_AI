from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from food_ai.barcode.service import lookup_local_barcode, store_openfood_product
from food_ai.config import SCHEMA_PATH
from food_ai.core.daily_totals import get_daily_totals
from food_ai.core.log_consumption import log_consumption
from food_ai.food_bridge.config import DB_PATH
from food_ai.food_bridge.nutrition_bridge import NUTRIENT_KEYS, build_food_bridge


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if row is None:
        return 0
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _validate_bridge_totals(db_path: Path, dish: str) -> dict[str, Any]:
    result = build_food_bridge(
        [{"label": dish, "name": dish.replace("_", " "), "confidence": 0.95}],
        db_path=db_path,
        top_k_ingredients=20,
        use_dish_mass_priors=True,
    )
    palm_ready = result.palm_ready or {}
    estimated = palm_ready.get("estimated_nutrition_per_100g") or {}
    matched = palm_ready.get("matched_ingredients") or []
    mismatches: list[str] = []
    for key in NUTRIENT_KEYS:
        expected = estimated.get(key)
        if expected is None:
            continue
        actual = sum(float((row.get("nutrient_contributions") or {}).get(f"{key}_contrib") or 0.0) for row in matched)
        if abs(actual - float(expected)) > 0.05:
            mismatches.append(key)
    return {
        "status": result.status,
        "warnings": result.warnings,
        "nutrition_basis": palm_ready.get("nutrition_basis"),
        "matched_count": len(matched),
        "contribution_mismatches": mismatches,
        "top_ingredients": [
            {
                "ingredient": row.get("ingredient"),
                "grams_default": row.get("grams_default"),
                "grams_per_100g_dish": row.get("grams_per_100g_dish"),
                "calories_contrib": (row.get("nutrient_contributions") or {}).get("calories_contrib"),
            }
            for row in matched[:6]
        ],
    }


def _barcode_flow_smoke() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "barcode_smoke.db"
        with closing(sqlite3.connect(db_path)) as connection:
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            connection.commit()
        product = {
            "barcode": "1234567890123",
            "brand": "PALM Smoke",
            "product_name": "Smoke Test Bar",
            "category": "test",
            "source": "smoke",
            "verified": 1,
            "nutrition": {
                "calories": 250,
                "protein_g": 10,
                "total_fat_g": 5,
                "total_carbs_g": 35,
            },
            "serving_sizes": [{"serving_name": "100 g", "grams": 100, "is_default": 1}],
        }
        store_openfood_product(product, db_path)
        lookup = lookup_local_barcode(product["barcode"], db_path)
        log_result = log_consumption(product["barcode"], 100, db_path)
        daily = get_daily_totals(db_path)
    return {
        "lookup_status": "found" if lookup else "missing",
        "log_status": log_result.get("status"),
        "daily_calories": (daily.get("nutrition_totals") or {}).get("calories"),
    }


def run_smoke(db_path: str | Path = DB_PATH, dish: str = "lasagna") -> dict[str, Any]:
    database_path = Path(db_path)
    with closing(sqlite3.connect(database_path)) as connection:
        dishes_count = _table_count(connection, "dishes")
        priors_count = _table_count(connection, "dish_ingredient_priors")
        invalid_dish_ids = connection.execute(
            """
            SELECT COUNT(*)
            FROM dish_ingredient_priors p
            LEFT JOIN dishes d ON d.dish_id = p.dish_id
            WHERE d.dish_id IS NULL
            """
        ).fetchone()[0] if priors_count else 0
        missing_grams = connection.execute(
            """
            SELECT COUNT(*)
            FROM dish_ingredient_priors
            WHERE default_include = 1
              AND (grams_default IS NULL OR grams_default <= 0 OR portion_method = 'missing')
            """
        ).fetchone()[0] if priors_count else 0
        missing_grams_per_100g = connection.execute(
            """
            SELECT COUNT(*)
            FROM dish_ingredient_priors
            WHERE default_include = 1
              AND (grams_per_100g_dish IS NULL OR grams_per_100g_dish <= 0)
            """
        ).fetchone()[0] if priors_count else 0

    return {
        "db_path": str(database_path),
        "dishes_seeded": dishes_count,
        "dish_ingredient_priors": priors_count,
        "invalid_prior_dish_ids": int(invalid_dish_ids),
        "included_priors_missing_grams_default": int(missing_grams),
        "included_priors_missing_grams_per_100g_dish": int(missing_grams_per_100g),
        "bridge": _validate_bridge_totals(database_path, dish) if priors_count else None,
        "barcode_product_flow": _barcode_flow_smoke(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test DB-backed dish priors and barcode nutrition flow.")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--dish", default="lasagna")
    args = parser.parse_args()
    print("PALM DB Dish Prior Smoke Test")
    print("=" * 32)
    print(json.dumps(run_smoke(args.db_path, args.dish), indent=2))


if __name__ == "__main__":
    main()
