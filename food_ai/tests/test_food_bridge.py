from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from food_ai.food_bridge.ingredient_normalize import normalize_ingredient
from food_ai.food_bridge.nutrition_bridge import build_food_bridge


class FoodBridgeTests(unittest.TestCase):
    def test_normalize_common_aliases(self) -> None:
        self.assertEqual(normalize_ingredient("2 cups chopped mozzarella cheese"), "mozzarella")
        self.assertEqual(normalize_ingredient("1 tbsp olive oil"), "olive oil")
        self.assertEqual(normalize_ingredient("3 cups flour"), "wheat flour")
        self.assertEqual(normalize_ingredient("1 lb ground beef"), "ground beef")

    def test_build_food_bridge_blends_priors_and_nutrition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            priors_path = root / "dish_priors.json"
            db_path = root / "palm.db"
            priors_path.write_text(
                json.dumps(
                    {
                        "metadata": {"source": "test"},
                        "dish_priors": {
                            "pizza": [
                                {"ingredient": "mozzarella", "weight": 0.6, "source_count": 6},
                                {"ingredient": "tomato sauce", "weight": 0.4, "source_count": 4},
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE generic_ingredient_nutrition (
                        ingredient TEXT PRIMARY KEY,
                        calories REAL,
                        protein_g REAL,
                        fat_g REAL,
                        carbs_g REAL,
                        fiber_g REAL,
                        sugar_g REAL,
                        sodium_mg REAL,
                        source TEXT,
                        fdc_id INTEGER,
                        description TEXT
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO generic_ingredient_nutrition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("mozzarella", 280, 28, 17, 3, 0, 1, 620, "test", 1, "Mozzarella"),
                )
                connection.execute(
                    "INSERT INTO generic_ingredient_nutrition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("tomato sauce", 40, 2, 0.5, 8, 2, 5, 300, "test", 2, "Tomato sauce"),
                )
                connection.commit()

            result = build_food_bridge(
                [{"label": "pizza", "name": "pizza", "confidence": 0.85}],
                db_path=db_path,
                dish_priors_path=priors_path,
            )

            self.assertEqual(result.status, "ready")
            self.assertEqual(len(result.ingredients_ranked), 2)
            self.assertEqual(result.ingredient_normalization["summary"]["matched_count"], 2)
            self.assertEqual(result.palm_ready["matched_ingredients"][0]["match_method"], "exact")
            nutrition = result.palm_ready["estimated_nutrition_per_100g"]
            self.assertAlmostEqual(nutrition["calories"], 184.0)
            self.assertAlmostEqual(nutrition["protein_g"], 17.6)
            self.assertAlmostEqual(nutrition["total_carbs_g"], 5.0)

    def test_build_food_bridge_uses_local_nutrition_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            priors_path = root / "dish_priors.json"
            db_path = root / "palm.db"
            priors_path.write_text(
                json.dumps(
                    {
                        "metadata": {"source": "test"},
                        "dish_priors": {
                            "test_dish": [
                                {"ingredient": "egg", "weight": 0.25, "source_count": 1},
                                {"ingredient": "wheat flour", "weight": 0.25, "source_count": 1},
                                {"ingredient": "vanilla", "weight": 0.25, "source_count": 1},
                                {"ingredient": "butter", "weight": 0.25, "source_count": 1},
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE generic_ingredient_nutrition (
                        ingredient TEXT PRIMARY KEY,
                        calories REAL,
                        protein_g REAL,
                        fat_g REAL,
                        carbs_g REAL,
                        source TEXT,
                        fdc_id INTEGER
                    )
                    """
                )
                for index, ingredient in enumerate(["egg whole", "all purpose flour", "vanilla extract", "butter unsalted"], start=1):
                    connection.execute(
                        "INSERT INTO generic_ingredient_nutrition VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (ingredient, 10 * index, index, index, index, "test", index),
                    )
                connection.commit()

            result = build_food_bridge(
                [{"label": "test_dish", "name": "test dish", "confidence": 1.0}],
                db_path=db_path,
                dish_priors_path=priors_path,
                top_k_ingredients=4,
            )

            self.assertEqual(result.status, "ready")
            self.assertEqual(result.ingredient_normalization["summary"]["matched_count"], 4)
            matched = {item["ingredient"]: item for item in result.palm_ready["matched_ingredients"]}
            self.assertEqual(matched["egg"]["matched_ingredient"], "egg whole")
            self.assertEqual(matched["wheat flour"]["matched_ingredient"], "all purpose flour")
            self.assertEqual(matched["vanilla"]["matched_ingredient"], "vanilla extract")
            self.assertEqual(matched["butter"]["matched_ingredient"], "butter unsalted")
            self.assertTrue(all(item["match_method"] == "alias" for item in matched.values()))

    def test_build_food_bridge_sums_fat_components_when_total_fat_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            priors_path = root / "dish_priors.json"
            db_path = root / "palm.db"
            priors_path.write_text(
                json.dumps(
                    {
                        "metadata": {"source": "test"},
                        "dish_priors": {
                            "test_dish": [
                                {"ingredient": "test oil", "weight": 1.0, "source_count": 1},
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE generic_ingredient_nutrition (
                        ingredient TEXT PRIMARY KEY,
                        calories REAL,
                        protein_g REAL,
                        fat_g REAL,
                        sat_fat_g REAL,
                        mono_fat_g REAL,
                        poly_fat_g REAL,
                        trans_fat_g REAL,
                        carbs_g REAL,
                        source TEXT,
                        fdc_id INTEGER
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO generic_ingredient_nutrition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("test oil", 120, 0, None, 2.5, 4.0, 3.0, 0.5, 0, "test", 1),
                )
                connection.commit()

            result = build_food_bridge(
                [{"label": "test_dish", "name": "test dish", "confidence": 1.0}],
                db_path=db_path,
                dish_priors_path=priors_path,
                top_k_ingredients=1,
            )

            self.assertEqual(result.status, "ready")
            nutrition = result.palm_ready["estimated_nutrition_per_100g"]
            self.assertAlmostEqual(nutrition["total_fat_g"], 10.0)
            matched = result.palm_ready["matched_ingredients"][0]
            self.assertTrue(matched["total_fat_fallback_applied"])

    def test_build_food_bridge_uses_v2_runtime_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "PALM_V2.db"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE canonical_foods (
                        canonical_food_id INTEGER PRIMARY KEY,
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
                        created_at TEXT NOT NULL
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
                        derivation_quality_score REAL NOT NULL
                    );
                    CREATE TABLE dish_labels (
                        dish_key TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        canonical_food_id INTEGER,
                        source TEXT NOT NULL,
                        confidence_score REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE dish_aliases (
                        alias_name TEXT PRIMARY KEY,
                        dish_key TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL
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
                        confidence_score REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO canonical_foods (
                        canonical_food_id,
                        canonical_name,
                        prep_state,
                        food_role,
                        preferred_fdc_id,
                        preferred_source,
                        food_category_id,
                        confidence_score,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (1, "mozzarella", "unspecified", "ingredient", 329370, "foundation_food", 1, 1.0, "2026-05-28T00:00:00Z", "2026-05-28T00:00:00Z"),
                        (2, "tomato sauce", "unspecified", "ingredient", 332282, "foundation_food", 6, 1.0, "2026-05-28T00:00:00Z", "2026-05-28T00:00:00Z"),
                    ],
                )
                connection.executemany(
                    "INSERT INTO canonical_food_aliases VALUES (?, ?, ?, ?, ?)",
                    [
                        ("mozzarella", 1, "test", "test", "2026-05-28T00:00:00Z"),
                        ("tomato sauce", 2, "test", "test", "2026-05-28T00:00:00Z"),
                    ],
                )
                connection.executemany(
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
                    [
                        (1, 280, 28, 17, 11, 3, 0, 1, 620, 65, 693, 0.2, 116, 329370, 1.0, 0.95),
                        (2, 40, 2, 0.5, 0.1, 8, 2, 5, 300, 0, 30, 0.8, 220, 332282, 1.0, 0.9),
                    ],
                )
                connection.execute(
                    "INSERT INTO dish_labels VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("pizza", "Pizza", None, "fndds_input_food", 0.95, "2026-05-28T00:00:00Z", "2026-05-28T00:00:00Z"),
                )
                connection.execute(
                    "INSERT INTO dish_aliases VALUES (?, ?, ?, ?)",
                    ("pizza", "pizza", "test", "2026-05-28T00:00:00Z"),
                )
                connection.executemany(
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
                        confidence_score,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("pizza", 1, 60.0, 60.0, 60.0, 0.9, 10, "fndds_input_food", 0.95, "2026-05-28T00:00:00Z", "2026-05-28T00:00:00Z"),
                        ("pizza", 2, 40.0, 40.0, 40.0, 0.8, 10, "fndds_input_food", 0.9, "2026-05-28T00:00:00Z", "2026-05-28T00:00:00Z"),
                    ],
                )
                connection.commit()

            result = build_food_bridge(
                [{"label": "pizza", "name": "pizza", "confidence": 0.85}],
                db_path=db_path,
                top_k_ingredients=2,
            )

            self.assertEqual(result.status, "ready")
            self.assertEqual(result.palm_ready["nutrition_basis"], "dish_ingredient_priors_grams")
            self.assertEqual(result.palm_ready["db_dish_prior_summary"]["dish_label"], "pizza")
            self.assertEqual(result.ingredient_normalization["summary"]["matched_count"], 2)
            nutrition = result.palm_ready["estimated_nutrition_per_100g"]
            self.assertAlmostEqual(nutrition["calories"], 184.0)
            self.assertAlmostEqual(nutrition["protein_g"], 17.6)
            self.assertAlmostEqual(nutrition["total_carbs_g"], 5.0)

    def test_missing_priors_returns_pending_status(self) -> None:
        result = build_food_bridge(
            [{"label": "pizza", "name": "pizza", "confidence": 0.85}],
            dish_priors_path=Path("C:/PALM/artifacts/food_bridge/does_not_exist.json"),
        )

        self.assertEqual(result.status, "pending")
        self.assertIn("Dish priors artifact missing", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
