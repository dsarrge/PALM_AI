from __future__ import annotations

from contextlib import closing
import os
import shutil
import sqlite3
import unittest

import pandas as pd
from pathlib import Path
from uuid import uuid4

from food_ai.barcode.openfoodfacts import _extract_serving_sizes
from food_ai.barcode.service import store_openfood_product
from food_ai.generic_foods.service import build_generic_foods
from food_ai.nutrition import normalize_nutrition
from food_ai.vision import build_dish_reference_index, infer_dish
from food_ai.sync.usda_ingredient_nutrition_loader import refine_specific_candidates, select_representative_fdc
from food_ai.usda.matching import lexical_match_score
from food_ai.utils.ingredient_normalize import normalize_ingredient_line, normalize_ingredient_text
from food_ai.utils.ingredient_parser import (
    CHOICE,
    GENERIC_FAT,
    GENERIC_NUT,
    PRODUCT_MIX,
    SEASONING_COMBO,
    parse_ingredient_line,
)
from food_ai.utils.usda_normalize import normalize_usda_ingredient


WORKSPACE_TEMP_DIR = Path(__file__).resolve().parents[2] / "artifacts" / "test_tmp"
WORKSPACE_TEMP_DIR.mkdir(parents=True, exist_ok=True)


def new_db_path(prefix: str) -> Path:
    return WORKSPACE_TEMP_DIR / f"{prefix}_{uuid4().hex}.db"


class NutritionRefactorTests(unittest.TestCase):
    def test_normalize_nutrition_supports_total_macro_fields(self) -> None:
        normalized = normalize_nutrition(
            {
                "calories": 250,
                "protein_g": 10,
                "total_fat_g": 5,
                "total_carbs_g": 30,
            }
        )
        self.assertEqual(normalized["derived"]["calories_from_macros"], 205.0)
        self.assertAlmostEqual(normalized["derived"]["fat_pct"], 45.0 / 205.0)
        self.assertTrue(normalized["quality"]["label_consistency"])

    def test_extract_serving_size_from_text(self) -> None:
        servings = _extract_serving_sizes("2 bars (40 g)")
        self.assertEqual(len(servings), 1)
        self.assertEqual(servings[0].grams, 40.0)


class BarcodeStorageTests(unittest.TestCase):
    def test_store_openfood_product_is_idempotent_by_barcode(self) -> None:
        db_path = new_db_path("barcode")
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE products (
                        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        brand TEXT,
                        product_name TEXT NOT NULL,
                        category TEXT,
                        source TEXT,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        raw_nutrition_json TEXT
                    );
                    CREATE TABLE barcodes (
                        barcode TEXT PRIMARY KEY,
                        product_id INTEGER NOT NULL,
                        format TEXT,
                        verified INTEGER DEFAULT 0
                    );
                    CREATE TABLE serving_sizes (
                        serving_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        product_id INTEGER NOT NULL,
                        serving_name TEXT,
                        grams REAL NOT NULL,
                        is_default INTEGER DEFAULT 0
                    );
                    CREATE TABLE nutrition_facts (
                        product_id INTEGER PRIMARY KEY,
                        calories REAL,
                        protein_g REAL,
                        total_fat_g REAL,
                        saturated_fat_g REAL,
                        trans_fat_g REAL,
                        total_carbs_g REAL,
                        fiber_g REAL,
                        total_sugars_g REAL,
                        added_sugars_g REAL,
                        sodium_mg REAL,
                        cholesterol_mg REAL
                    );
                    CREATE TABLE nutrition_micros (
                        product_id INTEGER PRIMARY KEY,
                        vitamin_d_mcg REAL,
                        calcium_mg REAL,
                        iron_mg REAL,
                        potassium_mg REAL,
                        caffeine_mg REAL
                    );
                    CREATE TABLE unknown_barcodes (
                        barcode TEXT PRIMARY KEY,
                        first_seen TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )

            product = {
                "barcode": "1234567890123",
                "product_name": "Protein Bar",
                "brand": "PALM",
                "nutrition": {
                    "calories": 250,
                    "protein_g": 10,
                    "total_fat_g": 5,
                    "total_carbs_g": 30,
                },
                "serving_sizes": [
                    {"serving_name": "1 bar (50 g)", "grams": 50.0, "is_default": 1}
                ],
            }

            first_product_id = store_openfood_product(product, db_path=db_path)
            second_product_id = store_openfood_product(product, db_path=db_path)
            self.assertEqual(first_product_id, second_product_id)

            with closing(sqlite3.connect(db_path)) as connection:
                product_count = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
                serving_count = connection.execute("SELECT COUNT(*) FROM serving_sizes").fetchone()[0]

            self.assertEqual(product_count, 1)
            self.assertEqual(serving_count, 1)
        finally:
            if db_path.exists():
                os.remove(db_path)


class GenericFoodBuilderTests(unittest.TestCase):
    def test_build_generic_foods_tolerates_minimal_schema(self) -> None:
        db_path = new_db_path("generic_foods")
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE generic_foods (
                        food_id INTEGER PRIMARY KEY,
                        food_name TEXT NOT NULL,
                        ingredient TEXT NOT NULL,
                        prep_state TEXT,
                        source TEXT,
                        created_at TEXT
                    );
                    CREATE TABLE generic_food_nutrition (
                        food_id INTEGER PRIMARY KEY,
                        calories REAL,
                        protein_g REAL,
                        total_fat_g REAL,
                        total_carbs_g REAL
                    );
                    CREATE TABLE generic_ingredient_nutrition (
                        ingredient TEXT PRIMARY KEY,
                        calories REAL,
                        protein_g REAL,
                        fat_g REAL,
                        carbs_g REAL,
                        source TEXT
                    );
                    INSERT INTO generic_ingredient_nutrition (
                        ingredient,
                        calories,
                        protein_g,
                        fat_g,
                        carbs_g,
                        source
                    ) VALUES ('rice', 130, 2.7, 0.3, 28.0, 'USDA');
                    """
                )

            built_count = build_generic_foods(db_path=db_path)
            self.assertEqual(built_count, 1)

            with closing(sqlite3.connect(db_path)) as connection:
                row = connection.execute(
                    "SELECT calories, protein_g, total_fat_g, total_carbs_g FROM generic_food_nutrition"
                ).fetchone()

            self.assertEqual(row, (130.0, 2.7, 0.3, 28.0))
        finally:
            if db_path.exists():
                os.remove(db_path)



class PreNormalizationParserTests(unittest.TestCase):
    def test_parser_splits_seasoning_combo(self) -> None:
        parsed = parse_ingredient_line("salt and freshly ground pepper to taste")
        self.assertEqual([fragment.text for fragment in parsed.fragments], ["salt", "pepper"])
        self.assertTrue(all(fragment.ambiguity_class == SEASONING_COMBO for fragment in parsed.fragments))

    def test_parser_classifies_choice_and_ambiguous_singletons(self) -> None:
        choice = parse_ingredient_line("2 tbsp. butter or margarine")
        self.assertEqual([fragment.text for fragment in choice.fragments], ["2 tbsp. butter", "margarine"])
        self.assertTrue(all(fragment.ambiguity_class == CHOICE for fragment in choice.fragments))

        generic_fat = parse_ingredient_line("1/2 c. oil")
        self.assertEqual(generic_fat.fragments[0].ambiguity_class, GENERIC_FAT)

        generic_nut = parse_ingredient_line("1 c. chopped nuts")
        self.assertEqual(generic_nut.fragments[0].ambiguity_class, GENERIC_NUT)

        product_mix = parse_ingredient_line("1 box yellow cake mix")
        self.assertEqual(product_mix.fragments[0].ambiguity_class, PRODUCT_MIX)

    def test_normalize_ingredient_line_respects_ambiguity_classes(self) -> None:
        self.assertEqual(normalize_ingredient_line("salt and pepper to taste"), ["salt", "pepper"])
        self.assertEqual(normalize_ingredient_line("2 tbsp. butter or margarine"), [])
        self.assertEqual(
            normalize_ingredient_line("2 tbsp. butter or margarine", include_choices=True),
            ["butter unsalted", "margarine"],
        )


class UsdaNormalizationTests(unittest.TestCase):
    def test_normalize_usda_ingredient_handles_structured_food_labels(self) -> None:
        self.assertEqual(normalize_usda_ingredient("Cheese, asiago"), "asiago cheese")
        self.assertEqual(normalize_usda_ingredient("Milk, almond, unsweetened"), "almond milk")
        self.assertEqual(normalize_usda_ingredient("Sauce, alfredo mix, dry"), "alfredo sauce")
        self.assertEqual(normalize_usda_ingredient("Bread, wheat"), "wheat bread")
        self.assertEqual(normalize_usda_ingredient("Sugars, brown"), "brown sugar")
        self.assertEqual(normalize_usda_ingredient("Sauce, worcestershire"), "worcestershire sauce")
        self.assertEqual(normalize_usda_ingredient("Leavening agents, baking powder, low-sodium"), "baking powder")
        self.assertEqual(normalize_usda_ingredient("Spices, bay leaf"), "bay leaf")
        self.assertEqual(normalize_usda_ingredient("Spices, cinnamon, ground"), "cinnamon")
        self.assertEqual(normalize_usda_ingredient("Cocoa, dry powder, unsweetened"), "cocoa powder")
        self.assertEqual(normalize_usda_ingredient("Vinegar, red wine"), "vinegar")
        self.assertEqual(normalize_usda_ingredient("Nuts, walnuts, english"), "walnut")
        self.assertEqual(normalize_usda_ingredient("Peppers, sweet, green, raw"), "bell pepper")
        self.assertEqual(normalize_usda_ingredient("Margarine, regular, hard, soybean (hydrogenated)"), "margarine")
        self.assertEqual(normalize_usda_ingredient("Tomatoes, red, ripe, raw, year round average"), "tomato")
        self.assertEqual(normalize_usda_ingredient("Beef, ground, 95% lean meat / 5% fat, raw"), "beef ground")
        self.assertEqual(normalize_usda_ingredient("Mayonnaise, made with tofu"), "mayonnaise")

    def test_lexical_match_prefers_true_category_terms(self) -> None:
        almond_milk_score = lexical_match_score("almond milk", "milk, almond, unsweetened")
        almond_butter_score = lexical_match_score("almond milk", "almond butter, creamy")
        self.assertGreater(almond_milk_score, almond_butter_score)
        self.assertGreater(almond_milk_score, 2.0)

    def test_lexical_match_penalizes_over_specific_generic_foods(self) -> None:
        simple_bread_score = lexical_match_score("bread", "bread, wheat")
        dish_bread_score = lexical_match_score("bread", "bread, pound cake type, pan de torta salvadoran")
        self.assertGreater(simple_bread_score, dish_bread_score)

    def test_lexical_match_penalizes_cross_animal_and_cut_mismatches(self) -> None:
        beef_loin_score = lexical_match_score("beef loin", "beef, loin, separable lean only, raw")
        pork_loin_score = lexical_match_score("beef loin", "pork, loin, boneless, raw")
        chicken_leg_score = lexical_match_score("chicken leg", "chicken, leg, meat and skin, raw")
        chicken_ground_score = lexical_match_score("chicken leg", "chicken, ground, raw")
        self.assertGreater(beef_loin_score, pork_loin_score)
        self.assertGreater(chicken_leg_score, chicken_ground_score)

    def test_lexical_match_penalizes_offal_and_missing_qualifiers(self) -> None:
        duck_score = lexical_match_score("duck", "duck, domesticated, liver, raw")
        duck_liver_score = lexical_match_score("duck liver", "duck, domesticated, liver, raw")
        chicken_broth_score = lexical_match_score("chicken broth", "chicken, canned, no broth")
        almond_milk_score = lexical_match_score("almond milk", "milk, sheep, fluid")
        beef_score = lexical_match_score("beef", "beef, grass-fed, ground, raw")
        generic_beef_score = lexical_match_score("beef", 'beef, composite of trimmed retail cuts, separable lean and fat, trimmed to 1/8" fat, all grades, raw')
        self.assertGreater(duck_liver_score, duck_score)
        self.assertLess(chicken_broth_score, 0.5)
        self.assertLess(almond_milk_score, 1.0)
        self.assertGreater(generic_beef_score, beef_score)

    def test_normalize_usda_ingredient_keeps_meat_cut_specificity(self) -> None:
        self.assertEqual(
            normalize_usda_ingredient('Beef, flank, steak, separable lean only, trimmed to 0" fat, choice, raw'),
            "beef flank steak",
        )
        self.assertEqual(
            normalize_usda_ingredient("Pork, fresh, loin, tenderloin, separable lean only, raw"),
            "pork tenderloin",
        )
        self.assertEqual(
            normalize_usda_ingredient("Chicken, wing, meat and skin, raw"),
            "chicken wing",
        )

    def test_normalize_usda_ingredient_does_not_collapse_offal_to_base_animal(self) -> None:
        self.assertEqual(normalize_usda_ingredient("Duck, domesticated, liver, raw"), "duck liver")
        self.assertEqual(normalize_usda_ingredient("Turkey, whole, giblets, raw"), "turkey giblet")
        self.assertIsNone(normalize_usda_ingredient("Pork, pickled pork hocks"))
        self.assertIsNone(normalize_usda_ingredient("Chicken, canned, no broth"))
        self.assertEqual(normalize_usda_ingredient("Soymilk, original and vanilla, unfortified"), "soy milk")
        self.assertEqual(normalize_usda_ingredient("Chicken, broilers or fryers, leg, meat and skin, raw"), "chicken leg")
        self.assertEqual(normalize_usda_ingredient("Soup, stock, chicken, home-prepared"), "chicken stock")
        self.assertEqual(normalize_usda_ingredient("Fish, snapper, mixed species, raw"), "snapper")
        self.assertEqual(normalize_usda_ingredient("Apricot nectar, canned, with added ascorbic acid"), "apricot juice")
        self.assertEqual(normalize_usda_ingredient("Lemons, raw, without peel"), "lemon")
        self.assertEqual(normalize_usda_ingredient("Oranges, raw, navels"), "orange")
        self.assertEqual(normalize_usda_ingredient("Peas, green, raw"), "pea")
        self.assertEqual(normalize_usda_ingredient("Bread, potato"), "potato bread")
        self.assertEqual(normalize_usda_ingredient("Cheese, cream, low fat"), "cream cheese")
        self.assertEqual(normalize_usda_ingredient("Milk, buttermilk, fluid, cultured, lowfat"), "buttermilk")
        self.assertEqual(normalize_usda_ingredient("Milk, sheep, fluid"), "sheep milk")
        self.assertEqual(normalize_usda_ingredient("Rice, white, long-grain, regular, raw, enriched"), "rice white")
        self.assertEqual(normalize_usda_ingredient("Rice flour, white, unenriched"), "rice flour")
        self.assertEqual(normalize_usda_ingredient("Fish oil, sardine"), "sardine oil")
        self.assertEqual(normalize_usda_ingredient("Oil, mustard"), "mustard oil")
        self.assertEqual(normalize_usda_ingredient("Mustard, prepared, yellow"), "mustard")
        self.assertEqual(normalize_usda_ingredient("Bagels, wheat"), "bagel")
        self.assertEqual(normalize_usda_ingredient("Peach nectar, canned, with added ascorbic acid"), "peach nectar")
        self.assertEqual(normalize_usda_ingredient("Peppers, sweet, red, sauteed"), "cooked bell pepper")
        self.assertEqual(normalize_usda_ingredient("Peas, green, cooked, boiled, drained, without salt"), "cooked pea")


class RecipeIngredientNormalizationTests(unittest.TestCase):
    def test_normalize_recipe_pantry_entities(self) -> None:
        cases = {
            "1 tsp. vanilla": "vanilla extract",
            "1/2 tsp. salt": "salt",
            "1 tsp. baking soda": "baking soda",
            "1 tsp. soda": "baking soda",
            "1 c. mayonnaise": "mayonnaise",
            "1 stick oleo": "margarine",
            "1 lb. hamburger": "beef ground",
            "1 tbsp. worcestershire sauce": "worcestershire sauce",
            "1 bay leaf": "bay leaf",
            "1 tbsp. cornstarch": "cornstarch",
            "1 c. raisins": "raisin",
            "1/2 c. chopped pecans": "pecan",
            "paprika": "paprika",
            "1 tsp. oregano": "oregano",
            "1 tsp. chili powder": "chili powder",
            "1/2 tsp. cloves": "clove",
            "1/2 c. cocoa": "cocoa powder",
            "1 c. chopped walnuts": "walnut",
            "1 tsp. dry mustard": "mustard",
            "2 tbsp. vinegar": "vinegar",
            "1/2 tsp. pepper": "pepper",
            "1 large diced green pepper": "bell pepper",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_ingredient_text(raw), expected)

    def test_normalize_recipe_rejects_combined_seasoning_lines(self) -> None:
        self.assertIsNone(normalize_ingredient_text("salt and pepper to taste"))

    def test_normalize_recipe_meat_cuts(self) -> None:
        cases = {
            "1 lb ground turkey": "turkey ground",
            "2 chicken thighs": "chicken thigh",
            "1 beef brisket": "beef brisket",
            "1 pork tenderloin": "pork tenderloin",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_ingredient_text(raw), expected)

    def test_normalize_recipe_cooked_produce_variants(self) -> None:
        cases = {
            "2 cooked tomatoes": "cooked tomato",
            "1 cooked green pepper": "cooked bell pepper",
            "1 cup cooked peas": "cooked pea",
            "1 plain bagle": "bagel",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_ingredient_text(raw), expected)


class UsdaSelectionTests(unittest.TestCase):
    def test_select_representative_fdc_marks_exact_bank_matches(self) -> None:
        food_catalog = pd.DataFrame(
            [
                {
                    "fdc_id": 1,
                    "description": "beef, ground, 95% lean meat / 5% fat, raw",
                    "category_name": "Beef Products",
                    "normalized_ingredient": "beef ground",
                },
                {
                    "fdc_id": 2,
                    "description": "beef, flank, steak, separable lean only, raw",
                    "category_name": "Beef Products",
                    "normalized_ingredient": "beef flank steak",
                },
            ]
        )

        selected = select_representative_fdc(food_catalog, "beef ground")

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["match_method"], "normalized_bank")
        self.assertEqual(selected["semantic_score"], 1.0)

    def test_select_representative_fdc_rejects_candidates_missing_required_tokens(self) -> None:
        food_catalog = pd.DataFrame(
            [
                {
                    "fdc_id": 1,
                    "description": "beef, tenderloin steak, raw",
                    "category_name": "Beef Products",
                    "normalized_ingredient": "beef tenderloin",
                },
                {
                    "fdc_id": 2,
                    "description": "milk, sheep, fluid",
                    "category_name": "Dairy and Egg Products",
                    "normalized_ingredient": "milk",
                },
            ]
        )

        self.assertIsNone(select_representative_fdc(food_catalog.iloc[[0]], "beef flank steak"))
        self.assertIsNone(select_representative_fdc(food_catalog.iloc[[1]], "almond milk"))

    def test_select_representative_fdc_requires_requested_animal_token(self) -> None:
        food_catalog = pd.DataFrame(
            [
                {
                    "fdc_id": 1,
                    "description": "quail, breast, meat only, raw",
                    "category_name": "Poultry Products",
                    "normalized_ingredient": "quail breast",
                }
            ]
        )

        self.assertIsNone(select_representative_fdc(food_catalog, "turkey breast"))

    def test_select_representative_fdc_aliases_prime_rib_and_pasta_to_safe_fallbacks(self) -> None:
        food_catalog = pd.DataFrame(
            [
                {
                    "fdc_id": 1,
                    "description": 'beef, rib, whole (ribs 6-12), separable lean and fat, trimmed to 1/8" fat, prime, raw',
                    "category_name": "Beef Products",
                    "normalized_ingredient": "beef rib",
                },
                {
                    "fdc_id": 2,
                    "description": "pasta, dry, unenriched",
                    "category_name": "Cereal Grains and Pasta",
                    "normalized_ingredient": "pasta",
                },
            ]
        )

        self.assertIsNotNone(select_representative_fdc(food_catalog.iloc[[0]], "prime rib"))
        self.assertIsNotNone(select_representative_fdc(food_catalog.iloc[[1]], "penne"))

    def test_select_representative_fdc_prefers_cooked_meat_and_fish_rows_by_default(self) -> None:
        meat_catalog = pd.DataFrame(
            [
                {
                    "fdc_id": 1,
                    "description": "chicken, leg, meat only, raw",
                    "category_name": "Poultry Products",
                    "normalized_ingredient": "chicken leg",
                },
                {
                    "fdc_id": 2,
                    "description": "chicken, leg, meat only, cooked, roasted",
                    "category_name": "Poultry Products",
                    "normalized_ingredient": "chicken leg",
                },
            ]
        )
        fish_catalog = pd.DataFrame(
            [
                {
                    "fdc_id": 3,
                    "description": "fish, snapper, mixed species, raw",
                    "category_name": "Fish and Shellfish Products",
                    "normalized_ingredient": "snapper",
                },
                {
                    "fdc_id": 4,
                    "description": "fish, snapper, mixed species, cooked, dry heat",
                    "category_name": "Fish and Shellfish Products",
                    "normalized_ingredient": "snapper",
                },
            ]
        )
        pork_catalog = pd.DataFrame(
            [
                {
                    "fdc_id": 5,
                    "description": "pork, fresh, variety meats and by-products, chitterlings, cooked, simmered",
                    "category_name": "Pork Products",
                    "normalized_ingredient": "pork",
                },
                {
                    "fdc_id": 6,
                    "description": "pork, fresh, composite of trimmed retail cuts (loin and shoulder blade), separable lean only, cooked",
                    "category_name": "Pork Products",
                    "normalized_ingredient": "pork",
                },
            ]
        )
        produce_catalog = pd.DataFrame(
            [
                {
                    "fdc_id": 7,
                    "description": "peppers, sweet, red, sauteed",
                    "category_name": "Vegetables and Vegetable Products",
                    "normalized_ingredient": "bell pepper",
                },
                {
                    "fdc_id": 8,
                    "description": "peppers, sweet, green, raw",
                    "category_name": "Vegetables and Vegetable Products",
                    "normalized_ingredient": "bell pepper",
                },
                {
                    "fdc_id": 9,
                    "description": "lemons, raw, without peel",
                    "category_name": "Fruits and Fruit Juices",
                    "normalized_ingredient": "lemon",
                },
                {
                    "fdc_id": 10,
                    "description": "lemon juice, raw",
                    "category_name": "Fruits and Fruit Juices",
                    "normalized_ingredient": "lemon juice",
                },
                {
                    "fdc_id": 11,
                    "description": "peas, green, raw",
                    "category_name": "Vegetables and Vegetable Products",
                    "normalized_ingredient": "pea",
                },
                {
                    "fdc_id": 12,
                    "description": "peas, green, cooked, boiled, drained, without salt",
                    "category_name": "Vegetables and Vegetable Products",
                    "normalized_ingredient": "pea green salt",
                },
                {
                    "fdc_id": 13,
                    "description": "grapefruit, raw, white, all areas",
                    "category_name": "Fruits and Fruit Juices",
                    "normalized_ingredient": "grapefruit",
                },
                {
                    "fdc_id": 14,
                    "description": "grapefruit juice, white, raw",
                    "category_name": "Fruits and Fruit Juices",
                    "normalized_ingredient": "grapefruit juice",
                },
            ]
        )

        selected_meat = select_representative_fdc(meat_catalog, "chicken leg")
        selected_fish = select_representative_fdc(fish_catalog, "snapper")
        selected_pork = select_representative_fdc(pork_catalog, "pork")
        selected_pepper = select_representative_fdc(produce_catalog.iloc[[0, 1]], "bell pepper")
        selected_lemon = select_representative_fdc(produce_catalog.iloc[[2, 3]], "lemon")
        selected_pea = select_representative_fdc(produce_catalog.iloc[[4, 5]], "pea")
        selected_grapefruit = select_representative_fdc(produce_catalog.iloc[[6, 7]], "grapefruit")

        self.assertIsNotNone(selected_meat)
        self.assertIsNotNone(selected_fish)
        self.assertIsNotNone(selected_pork)
        self.assertIsNotNone(selected_pepper)
        self.assertIsNotNone(selected_lemon)
        self.assertIsNotNone(selected_pea)
        self.assertIsNotNone(selected_grapefruit)
        assert selected_meat is not None
        assert selected_fish is not None
        assert selected_pork is not None
        assert selected_pepper is not None
        assert selected_lemon is not None
        assert selected_pea is not None
        assert selected_grapefruit is not None
        self.assertIn("cooked", selected_meat["description"])
        self.assertIn("cooked", selected_fish["description"])
        self.assertIn("composite of trimmed retail cuts", selected_pork["description"])
        self.assertIn("raw", selected_pepper["description"])
        self.assertIn("lemons, raw", selected_lemon["description"])
        self.assertIn("peas, green, raw", selected_pea["description"])
        self.assertIn("grapefruit, raw", selected_grapefruit["description"])

    def test_select_representative_fdc_accepts_plant_milk_bank_match(self) -> None:
        food_catalog = pd.DataFrame(
            [
                {
                    "fdc_id": 1,
                    "description": "almond milk, unsweetened, plain, shelf stable",
                    "category_name": "Beverages",
                    "normalized_ingredient": "almond milk",
                },
                {
                    "fdc_id": 2,
                    "description": "soymilk, original and vanilla, unfortified",
                    "category_name": "Legumes and Legume Products",
                    "normalized_ingredient": "soy milk",
                },
            ]
        )

        self.assertIsNotNone(select_representative_fdc(food_catalog, "almond milk"))
        self.assertIsNotNone(select_representative_fdc(food_catalog, "soy milk"))

    def test_refine_specific_candidates_blocks_narrow_false_fallbacks(self) -> None:
        cases = {
            "bread": (["bread, wheat", "bread, potato", "bread, cinnamon"], ["bread, wheat"]),
            "cream": (["cream, fluid", "cheese, cream"], ["cream, fluid"]),
            "milk": (["milk, fluid, low sodium", "milk, sheep, fluid"], ["milk, fluid, low sodium"]),
            "lowfat milk": (["milk, lowfat, fluid, 1% milkfat", "milk, buttermilk, fluid, cultured, lowfat"], ["milk, lowfat, fluid, 1% milkfat"]),
            "milk whole": (["milk, whole, 3.25% milkfat, with added vitamin d", "cheese, mozzarella, whole milk"], ["milk, whole, 3.25% milkfat, with added vitamin d"]),
            "peach juice": (["peach juice, canned or bottled, unsweetened, without added ascorbic acid", "peach nectar, canned"], ["peach juice, canned or bottled, unsweetened, without added ascorbic acid"]),
            "rice white": (["rice, white, long-grain, regular, raw, enriched", "rice flour, white, unenriched"], ["rice, white, long-grain, regular, raw, enriched"]),
            "sardine": (["sardines in water", "fish oil, sardine"], ["sardines in water"]),
            "mustard": (["mustard, prepared, yellow", "oil, mustard"], ["mustard, prepared, yellow"]),
            "bagel": (["bagels, plain", "bagel chips"], ["bagels, plain"]),
        }
        for ingredient, (descriptions, expected) in cases.items():
            with self.subTest(ingredient=ingredient):
                frame = pd.DataFrame({"description": descriptions})
                filtered = refine_specific_candidates(frame, ingredient)
                self.assertEqual(filtered["description"].tolist(), expected)


class CrossDatasetNormalizationTests(unittest.TestCase):
    def test_recipe_and_usda_normalizers_share_bank_keys(self) -> None:
        cases = [
            ("1 stick oleo", "Margarine, regular, hard, soybean (hydrogenated)", "margarine"),
            ("1 tsp. baking powder", "Leavening agents, baking powder, low-sodium", "baking powder"),
            ("1/2 c. brown sugar", "Sugars, brown", "brown sugar"),
            ("1 tbsp. worcestershire sauce", "Sauce, worcestershire", "worcestershire sauce"),
            ("1 bay leaf", "Spices, bay leaf", "bay leaf"),
            ("1 tsp. cinnamon", "Spices, cinnamon, ground", "cinnamon"),
            ("1/2 c. cocoa", "Cocoa, dry powder, unsweetened", "cocoa powder"),
            ("2 tbsp. vinegar", "Vinegar, red wine", "vinegar"),
            ("1 c. chopped walnuts", "Nuts, walnuts, english", "walnut"),
            ("1 large diced green pepper", "Peppers, sweet, green, raw", "bell pepper"),
            ("2 slices wheat bread", "Bread, wheat", "wheat bread"),
            ("1 lb. hamburger", "Beef, ground, 95% lean meat / 5% fat, raw", "beef ground"),
            ("2 tbsp. mayonnaise", "Mayonnaise, made with tofu", "mayonnaise"),
            ("1 tomato", "Tomatoes, red, ripe, raw, year round average", "tomato"),
        ]
        for recipe_text, usda_text, expected in cases:
            with self.subTest(recipe_text=recipe_text, usda_text=usda_text):
                self.assertEqual(normalize_ingredient_text(recipe_text), expected)
                self.assertEqual(normalize_usda_ingredient(usda_text), expected)


class DishInferenceTests(unittest.TestCase):
    def _write_dish_image(self, image_path: Path, base_color: tuple[int, int, int], accent_color: tuple[int, int, int]) -> None:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (96, 96), color=base_color)
        draw = ImageDraw.Draw(image)
        draw.ellipse((18, 18, 78, 78), fill=accent_color)
        draw.rectangle((8, 70, 88, 88), fill=tuple(min(channel + 20, 255) for channel in accent_color))
        image.save(image_path)

    def test_build_and_infer_dish_from_reference_gallery(self) -> None:
        gallery_root = WORKSPACE_TEMP_DIR / f"dish_gallery_{uuid4().hex}"
        index_path = gallery_root / "dish_index.npz"
        manifest_path = gallery_root / "dish_manifest.json"
        query_path = WORKSPACE_TEMP_DIR / f"query_salad_{uuid4().hex}.png"
        try:
            (gallery_root / "salad").mkdir(parents=True, exist_ok=True)
            (gallery_root / "pasta").mkdir(parents=True, exist_ok=True)

            self._write_dish_image(gallery_root / "salad" / "salad_1.png", (70, 140, 70), (210, 50, 50))
            self._write_dish_image(gallery_root / "salad" / "salad_2.png", (85, 150, 85), (190, 40, 40))
            self._write_dish_image(gallery_root / "pasta" / "pasta_1.png", (205, 170, 95), (170, 80, 45))
            self._write_dish_image(gallery_root / "pasta" / "pasta_2.png", (220, 185, 110), (155, 75, 30))
            self._write_dish_image(query_path, (78, 145, 78), (205, 55, 55))

            manifest = build_dish_reference_index(
                reference_dir=gallery_root,
                index_path=index_path,
                manifest_path=manifest_path,
            )
            result = infer_dish(query_path, index_path=index_path, top_k=2)

            self.assertEqual(manifest["image_count"], 4)
            self.assertEqual(result["dish"], "salad")
            self.assertEqual(result["candidates"][0]["dish"], "salad")
            self.assertGreater(result["confidence"], 0.5)
        finally:
            if gallery_root.exists():
                shutil.rmtree(gallery_root)
            if query_path.exists():
                query_path.unlink()

    def test_build_dish_reference_index_requires_labeled_subdirectories(self) -> None:
        gallery_root = WORKSPACE_TEMP_DIR / f"dish_gallery_flat_{uuid4().hex}"
        try:
            gallery_root.mkdir(parents=True, exist_ok=True)
            self._write_dish_image(gallery_root / "flat.png", (90, 90, 180), (210, 210, 80))

            with self.assertRaises(ValueError):
                build_dish_reference_index(reference_dir=gallery_root, index_path=gallery_root / "index.npz")
        finally:
            if gallery_root.exists():
                shutil.rmtree(gallery_root)

class FoodImagePipelineTests(unittest.TestCase):
    def test_score_dish_candidates_orders_by_similarity(self) -> None:
        import numpy as np

        from food_ai.vision.dish_retrieval import score_dish_candidates

        image_embedding = np.asarray([1.0, 0.0], dtype=np.float32)
        label_embeddings = np.asarray(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.8, 0.2],
            ],
            dtype=np.float32,
        )
        label_embeddings = label_embeddings / np.linalg.norm(label_embeddings, axis=1, keepdims=True)

        candidates = score_dish_candidates(
            image_embedding=image_embedding,
            label_embeddings=label_embeddings,
            labels=["pizza", "sushi", "fried_rice"],
            prompts=["a photo of pizza", "a photo of sushi", "a photo of fried rice"],
            top_k=2,
        )

        self.assertEqual([candidate.name for candidate in candidates], ["pizza", "fried rice"])

    def test_recipe_retrieval_reranks_matching_titles(self) -> None:
        import csv
        import numpy as np

        from food_ai.vision.dish_retrieval import DishCandidate
        from food_ai.vision.recipe_retrieval import retrieve_candidate_recipes

        class StubEmbedder:
            @staticmethod
            def build_prompt(text: str) -> str:
                return text

            @staticmethod
            def encode_texts(texts: list[str]) -> np.ndarray:
                vectors = []
                for text in texts:
                    lowered = text.lower()
                    if "chicken curry" in lowered:
                        vector = np.asarray([1.0, 0.0], dtype=np.float32)
                    elif "chicken soup" in lowered:
                        vector = np.asarray([0.6, 0.4], dtype=np.float32)
                    else:
                        vector = np.asarray([0.0, 1.0], dtype=np.float32)
                    vectors.append(vector / np.linalg.norm(vector))
                return np.vstack(vectors)

        recipe_csv_path = WORKSPACE_TEMP_DIR / f"recipe_candidates_{uuid4().hex}.csv"
        try:
            with recipe_csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["title", "ingredients", "directions", "link", "source", "NER"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "title": "Chicken Curry",
                            "ingredients": '["1 chicken breast", "1 onion", "1 cup rice"]',
                            "directions": '["Cook chicken", "Serve over rice"]',
                            "link": "https://example.com/curry",
                            "source": "test",
                            "NER": '["chicken breast", "onion", "rice"]',
                        },
                        {
                            "title": "Weeknight Chicken Curry",
                            "ingredients": '["2 chicken thighs", "1 onion"]',
                            "directions": '["Cook the curry"]',
                            "link": "https://example.com/weeknight-curry",
                            "source": "test",
                            "NER": '["chicken thigh", "onion"]',
                        },
                        {
                            "title": "Chicken Soup",
                            "ingredients": '["1 chicken breast", "2 carrots"]',
                            "directions": '["Simmer soup"]',
                            "link": "https://example.com/soup",
                            "source": "test",
                            "NER": '["chicken breast", "carrot"]',
                        },
                    ]
                )

            image_embedding = np.asarray([1.0, 0.0], dtype=np.float32)
            dish_candidates = [
                DishCandidate(
                    name="chicken curry",
                    label="chicken_curry",
                    score=0.95,
                    prompt="a photo of chicken curry",
                )
            ]

            recipes = retrieve_candidate_recipes(
                dish_candidates=dish_candidates,
                image_embedding=image_embedding,
                recipe_csv_path=recipe_csv_path,
                embedder=StubEmbedder(),
                top_k=2,
                max_recipes_per_dish=2,
                buffer_per_dish=4,
            )

            self.assertGreaterEqual(len(recipes), 1)
            self.assertIn("chicken curry", recipes[0].title.lower())
            self.assertEqual(recipes[0].matched_dish, "chicken curry")
        finally:
            if recipe_csv_path.exists():
                recipe_csv_path.unlink()

    def test_recipe_retrieval_prefilters_before_embedding_titles(self) -> None:
        import csv
        import numpy as np

        from food_ai.vision.dish_retrieval import DishCandidate
        from food_ai.vision.recipe_retrieval import retrieve_candidate_recipes

        class CountingEmbedder:
            def __init__(self) -> None:
                self.last_batch_size = 0

            @staticmethod
            def build_prompt(text: str) -> str:
                return text

            def encode_texts(self, texts: list[str]) -> np.ndarray:
                self.last_batch_size = len(texts)
                vectors = []
                for text in texts:
                    lowered = text.lower()
                    vector = np.asarray([1.0, 0.0], dtype=np.float32) if 'pizza' in lowered else np.asarray([0.0, 1.0], dtype=np.float32)
                    vectors.append(vector / np.linalg.norm(vector))
                return np.vstack(vectors)

        recipe_csv_path = WORKSPACE_TEMP_DIR / f"recipe_prefilter_{uuid4().hex}.csv"
        embedder = CountingEmbedder()
        try:
            with recipe_csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["title", "ingredients", "directions", "link", "source", "NER"],
                )
                writer.writeheader()
                writer.writerow({
                    "title": "Classic Pizza",
                    "ingredients": '["pizza dough", "cheese"]',
                    "directions": '["Bake"]',
                    "link": "https://example.com/pizza",
                    "source": "test",
                    "NER": '["pizza dough", "cheese"]',
                })
                writer.writerow({
                    "title": "Deep Dish Pizza",
                    "ingredients": '["pizza dough", "tomato sauce"]',
                    "directions": '["Bake"]',
                    "link": "https://example.com/deep-dish",
                    "source": "test",
                    "NER": '["pizza dough", "tomato sauce"]',
                })
                for index in range(40):
                    writer.writerow({
                        "title": f"Unrelated Recipe {index}",
                        "ingredients": '["water"]',
                        "directions": '["Stir"]',
                        "link": f"https://example.com/unrelated-{index}",
                        "source": "test",
                        "NER": '["water"]',
                    })

            recipes = retrieve_candidate_recipes(
                dish_candidates=[
                    DishCandidate(
                        name="pizza",
                        label="pizza",
                        score=0.9,
                        prompt="a photo of pizza",
                    )
                ],
                image_embedding=np.asarray([1.0, 0.0], dtype=np.float32),
                recipe_csv_path=recipe_csv_path,
                embedder=embedder,
                top_k=2,
                max_recipes_per_dish=2,
                buffer_per_dish=2,
                lexical_prefilter_per_dish=4,
            )

            self.assertGreaterEqual(len(recipes), 1)
            self.assertLess(embedder.last_batch_size, 10)
        finally:
            if recipe_csv_path.exists():
                recipe_csv_path.unlink()

    def test_palm_bridge_returns_nutrition_ready_output(self) -> None:
        from contextlib import closing
        import sqlite3

        from food_ai.vision.ingredient_ranker import rank_recipe_ingredients
        from food_ai.vision.palm_bridge import build_palm_bridge
        from food_ai.vision.recipe_retrieval import RecipeCandidate

        db_path = new_db_path("vision_bridge")
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(
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
                        description TEXT,
                        fdc_id INTEGER
                    );
                    INSERT INTO generic_ingredient_nutrition (
                        ingredient, calories, protein_g, fat_g, carbs_g, fiber_g, sugar_g, sodium_mg, source, description, fdc_id
                    ) VALUES
                        ('chicken breast', 165, 31, 3.6, 0, 0, 0, 74, 'USDA', 'Chicken breast', 1),
                        ('onion', 40, 1.1, 0.1, 9.3, 1.7, 4.2, 4, 'USDA', 'Onion', 2),
                        ('rice', 130, 2.7, 0.3, 28.0, 0.4, 0.1, 1, 'USDA', 'Rice', 3);
                    """
                )

            recipes = [
                RecipeCandidate(
                    title="Chicken Rice Bowl",
                    score=0.92,
                    matched_dish="fried rice",
                    title_score=0.7,
                    image_score=0.8,
                    link="",
                    source="test",
                    ingredients=("1 chicken breast", "1 onion", "1 cup rice"),
                    ner_ingredients=(),
                    directions=(),
                ),
                RecipeCandidate(
                    title="Simple Chicken and Rice",
                    score=0.81,
                    matched_dish="fried rice",
                    title_score=0.6,
                    image_score=0.7,
                    link="",
                    source="test",
                    ingredients=("2 chicken breasts", "1 onion", "2 cups rice"),
                    ner_ingredients=(),
                    directions=(),
                ),
            ]

            ranked = rank_recipe_ingredients(recipes, top_k=5)
            palm_ready = build_palm_bridge(ranked, db_path=db_path)

            self.assertEqual(palm_ready["status"], "ready")
            self.assertIn("rice", palm_ready["ingredient_graph"])
            self.assertIsNotNone(palm_ready["estimated_nutrition_per_100g"])
            self.assertGreaterEqual(len(palm_ready["matched_ingredients"]), 2)
        finally:
            if db_path.exists():
                os.remove(db_path)

class FoodVisionV1Tests(unittest.TestCase):
    def test_build_food101_model_sets_output_classes(self) -> None:
        from food_ai.vision.classifier import build_food101_model

        model = build_food101_model("efficientnet_b0", num_classes=7, pretrained=False)
        self.assertEqual(model.classifier[1].out_features, 7)

    def test_fuse_dish_candidates_combines_clip_and_classifier(self) -> None:
        from food_ai.vision.dish_retrieval import DishCandidate, fuse_dish_candidates

        clip_candidates = [
            DishCandidate(name="pizza", label="pizza", score=0.20, prompt="clip", source="clip", component_scores={"clip": 0.60}),
            DishCandidate(name="tacos", label="tacos", score=0.15, prompt="clip", source="clip", component_scores={"clip": 0.57}),
        ]
        classifier_candidates = [
            DishCandidate(name="pizza", label="pizza", score=0.85, prompt="classifier", source="classifier", component_scores={"classifier": 0.85}),
        ]

        fused = fuse_dish_candidates(clip_candidates, classifier_candidates, top_k=2)

        self.assertEqual(fused[0].label, "pizza")
        self.assertIn("classifier", fused[0].component_scores)
        self.assertIn("clip", fused[0].component_scores)

    def test_refinement_keeps_hidden_support_ingredients(self) -> None:
        from food_ai.vision.dish_retrieval import DishCandidate
        from food_ai.vision.recipe_retrieval import RecipeCandidate
        from food_ai.vision.refinement import refine_ingredients
        from food_ai.vision.ingredient_ranker import RankedIngredient

        ingredients = [
            RankedIngredient(
                ingredient="olive oil",
                confidence=0.7,
                recipe_hits=2,
                supporting_recipes=("Roasted Vegetables",),
                raw_examples=("olive oil",),
            ),
            RankedIngredient(
                ingredient="broccoli",
                confidence=0.9,
                recipe_hits=2,
                supporting_recipes=("Roasted Vegetables",),
                raw_examples=("broccoli",),
            ),
        ]
        recipes = [
            RecipeCandidate(
                title="Roasted Vegetables",
                score=0.8,
                matched_dish="roasted vegetables",
                title_score=0.7,
                image_score=0.6,
                link="",
                source="test",
                ingredients=("broccoli", "olive oil"),
                ner_ingredients=("broccoli", "olive oil"),
                directions=(),
            )
        ]
        dishes = [
            DishCandidate(name="roasted vegetables", label="roasted_vegetables", score=0.8, prompt="a photo of roasted vegetables")
        ]

        refined = refine_ingredients(ingredients, recipes, dishes)
        ingredient_names = [item["ingredient"] for item in refined["ingredients_ranked"]]
        olive_oil = next(item for item in refined["ingredients_ranked"] if item["ingredient"] == "olive oil")

        self.assertIn("olive oil", ingredient_names)
        self.assertIn("olive oil", ingredient_names)
        self.assertEqual(olive_oil["visibility_label"], "hidden_likely")

class FoodVisionCalibrationFusionTests(unittest.TestCase):
    def test_temperature_scaler_fit_save_and_load(self) -> None:
        import numpy as np

        from food_ai.vision.calibration.temperature import TemperatureScaler

        logits = np.asarray(
            [
                [2.4, 0.4, -0.2],
                [1.9, 0.3, -0.1],
                [0.2, 2.0, 0.1],
                [0.1, 1.8, 0.2],
            ],
            dtype=np.float32,
        )
        labels = np.asarray([0, 0, 1, 1], dtype=np.int64)

        scaler = TemperatureScaler().fit(logits, labels)
        artifact_path = WORKSPACE_TEMP_DIR / f"temperature_{uuid4().hex}.json"
        try:
            scaler.save(artifact_path)
            loaded = TemperatureScaler.load(artifact_path)
            probabilities = loaded.predict_proba(logits)

            self.assertGreater(loaded.temperature, 0.0)
            self.assertAlmostEqual(loaded.temperature, scaler.temperature, places=5)
            self.assertEqual(probabilities.shape, logits.shape)
            self.assertTrue(np.allclose(probabilities.sum(axis=1), 1.0))
        finally:
            if artifact_path.exists():
                artifact_path.unlink()

    def test_expected_calibration_error_handles_perfect_predictions(self) -> None:
        import numpy as np

        from food_ai.vision.calibration.metrics import expected_calibration_error

        probabilities = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        labels = np.asarray([0, 1], dtype=np.int64)

        self.assertEqual(expected_calibration_error(probabilities, labels, bins=5), 0.0)

    def test_confidence_summary_computes_agreement_and_entropy(self) -> None:
        from food_ai.vision.dish_retrieval import DishCandidate
        from food_ai.vision.fusion.confidence import compute_confidence_summary

        summary = compute_confidence_summary(
            classifier_probabilities={"pizza": 0.72, "tacos": 0.18, "salad": 0.10},
            retrieval_candidates=[
                DishCandidate(name="pizza", label="pizza", score=0.81, prompt="clip"),
                DishCandidate(name="tacos", label="tacos", score=0.74, prompt="clip"),
            ],
            calibrated=True,
        )

        self.assertAlmostEqual(summary["classifier_top1_prob"], 0.72)
        self.assertGreater(summary["classifier_margin"], 0.5)
        self.assertEqual(summary["cross_source_agreement"], 1.0)
        self.assertTrue(summary["calibrated"])

    def test_retrieval_score_normalization_sums_to_one(self) -> None:
        from food_ai.vision.dish_retrieval import DishCandidate
        from food_ai.vision.fusion.normalize import normalize_retrieval_scores

        normalized = normalize_retrieval_scores(
            [
                DishCandidate(name="pizza", label="pizza", score=0.7, prompt="clip"),
                DishCandidate(name="tacos", label="tacos", score=0.2, prompt="clip"),
                DishCandidate(name="salad", label="salad", score=-0.1, prompt="clip"),
            ],
            method="minmax",
        )

        self.assertAlmostEqual(sum(normalized.values()), 1.0)
        self.assertGreater(normalized["pizza"], normalized["tacos"])

    def test_merge_dish_predictions_prefers_confident_classifier(self) -> None:
        from food_ai.vision.dish_retrieval import DishCandidate
        from food_ai.vision.fusion.fusion import merge_dish_predictions

        fused, metadata = merge_dish_predictions(
            classifier_preds=[
                DishCandidate(name="pizza", label="pizza", score=0.88, prompt="classifier", source="classifier"),
                DishCandidate(name="tacos", label="tacos", score=0.08, prompt="classifier", source="classifier"),
            ],
            retrieval_preds=[
                DishCandidate(name="tacos", label="tacos", score=0.79, prompt="clip", source="clip"),
                DishCandidate(name="pizza", label="pizza", score=0.64, prompt="clip", source="clip"),
            ],
            classifier_confidence={
                "classifier_top1_prob": 0.88,
                "classifier_margin": 0.80,
                "classifier_entropy_inverted": 0.92,
                "cross_source_agreement": 1.0,
                "calibrated": True,
            },
            top_k=2,
        )

        self.assertEqual(fused[0].label, "pizza")
        self.assertGreater(metadata["classifier_weight"], metadata["retrieval_weight"])

    def test_merge_dish_predictions_falls_back_when_classifier_missing(self) -> None:
        from food_ai.vision.dish_retrieval import DishCandidate
        from food_ai.vision.fusion.fusion import merge_dish_predictions

        fused, metadata = merge_dish_predictions(
            classifier_preds=[],
            retrieval_preds=[
                DishCandidate(name="tacos", label="tacos", score=0.79, prompt="clip", source="clip"),
                DishCandidate(name="pizza", label="pizza", score=0.64, prompt="clip", source="clip"),
            ],
            classifier_confidence=None,
            top_k=2,
        )

        self.assertEqual(fused[0].label, "tacos")
        self.assertIn("classifier_missing", metadata["fallbacks_used"])
        self.assertEqual(metadata["classifier_weight"], 0.0)

    def test_merge_dish_predictions_preserves_diversity_when_classifier_is_uncertain(self) -> None:
        from food_ai.vision.dish_retrieval import DishCandidate
        from food_ai.vision.fusion.fusion import merge_dish_predictions

        fused, metadata = merge_dish_predictions(
            classifier_preds=[
                DishCandidate(name="pizza", label="pizza", score=0.34, prompt="classifier", source="classifier"),
                DishCandidate(name="salad", label="salad", score=0.33, prompt="classifier", source="classifier"),
            ],
            retrieval_preds=[
                DishCandidate(name="tacos", label="tacos", score=0.80, prompt="clip", source="clip"),
                DishCandidate(name="pizza", label="pizza", score=0.62, prompt="clip", source="clip"),
            ],
            classifier_confidence={
                "classifier_top1_prob": 0.34,
                "classifier_margin": 0.01,
                "classifier_entropy_inverted": 0.05,
                "cross_source_agreement": 0.0,
                "calibrated": False,
            },
            top_k=3,
        )

        labels = [candidate.label for candidate in fused]
        self.assertIn("tacos", labels[:2])
        self.assertIn("pizza", labels[:2])
        self.assertIn("low_classifier_confidence", metadata["fallbacks_used"])

    def test_pipeline_outputs_calibration_and_fusion_metadata(self) -> None:
        import csv

        from food_ai.vision.pipeline import infer_food_image

        class StubEmbedder:
            model_name = "stub-clip"

            @staticmethod
            def encode_image(_image_path: str):
                import numpy as np

                return np.asarray([1.0, 0.0], dtype=np.float32)

            @staticmethod
            def build_prompt(text: str) -> str:
                return text

            @staticmethod
            def encode_texts(texts: list[str]):
                import numpy as np

                vectors = []
                for text in texts:
                    lowered = text.lower()
                    if "pizza" in lowered:
                        vector = np.asarray([1.0, 0.0], dtype=np.float32)
                    elif "taco" in lowered:
                        vector = np.asarray([0.85, 0.15], dtype=np.float32)
                    else:
                        vector = np.asarray([0.1, 0.9], dtype=np.float32)
                    vectors.append(vector / np.linalg.norm(vector))
                return np.vstack(vectors)

        class StubClassifier:
            def predict_distribution(self, _image_path: str, top_k: int = 5, **_kwargs):
                from food_ai.vision.dish_retrieval import DishCandidate

                candidates = [
                    DishCandidate(name="pizza", label="pizza", score=0.61, prompt="classifier:pizza", source="classifier", component_scores={"classifier": 0.61}),
                    DishCandidate(name="tacos", label="tacos", score=0.28, prompt="classifier:tacos", source="classifier", component_scores={"classifier": 0.28}),
                ]
                return {
                    "top_k_candidates": candidates[:top_k],
                    "probabilities": {"pizza": 0.61, "tacos": 0.28, "salad": 0.11},
                    "raw_probabilities": {"pizza": 0.58, "tacos": 0.27, "salad": 0.15},
                    "calibrated_probabilities": None,
                    "calibrated": False,
                    "temperature": None,
                    "calibration_artifact_path": None,
                    "logits": [1.0, 0.0, -0.2],
                }

        recipe_csv_path = WORKSPACE_TEMP_DIR / f"pipeline_recipes_{uuid4().hex}.csv"
        try:
            with recipe_csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["title", "ingredients", "directions", "link", "source", "NER"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "title": "Simple Pizza",
                            "ingredients": '["pizza dough", "tomato sauce", "cheese"]',
                            "directions": '["Bake pizza"]',
                            "link": "https://example.com/pizza",
                            "source": "test",
                            "NER": '["pizza dough", "tomato sauce", "cheese"]',
                        },
                        {
                            "title": "Weeknight Tacos",
                            "ingredients": '["tortilla", "beef", "salsa"]',
                            "directions": '["Assemble tacos"]',
                            "link": "https://example.com/tacos",
                            "source": "test",
                            "NER": '["tortilla", "beef", "salsa"]',
                        },
                    ]
                )

            result = infer_food_image(
                image_path="food_ai/tests/food_1.jpg",
                recipe_data_path=recipe_csv_path,
                embedder=StubEmbedder(),
                classifier_predictor=StubClassifier(),
                calibration_enabled=True,
                local_files_only=True,
                top_k_dishes=3,
                top_k_recipes=2,
                top_k_ingredients=4,
            )

            self.assertIn("dish_classifier_topk", result)
            self.assertIn("retrieval_candidates", result)
            self.assertIn("dish_fused_topk", result)
            self.assertIn("dish_fusion", result)
            self.assertIn("confidence", result)
            self.assertFalse(result["dish_classifier_calibrated"])
            self.assertIn("calibration_missing", result["dish_fusion"]["fallbacks_used"])
        finally:
            if recipe_csv_path.exists():
                recipe_csv_path.unlink()

    def test_temperature_scaler_class_metadata_mismatch_is_detected(self) -> None:
        from food_ai.vision.calibration.temperature import TemperatureScaler

        scaler = TemperatureScaler(temperature=1.7, fitted=True, class_names=["pizza", "tacos"])

        self.assertTrue(scaler.matches_classes(["pizza", "tacos"]))
        self.assertFalse(scaler.matches_classes(["pizza", "salad"]))

    def test_load_validation_logits_artifact_rejects_bad_shapes(self) -> None:
        import numpy as np

        from food_ai.vision.calibration.fit_temperature import load_validation_logits_artifact

        artifact_path = WORKSPACE_TEMP_DIR / f"bad_logits_{uuid4().hex}.npz"
        try:
            with artifact_path.open("wb") as handle:
                np.savez_compressed(
                    handle,
                    logits=np.asarray([1.0, 2.0, 3.0], dtype=np.float32),
                    labels=np.asarray([0, 1], dtype=np.int64),
                    class_names=np.asarray(["pizza", "tacos"], dtype=object),
                )

            with self.assertRaises(ValueError):
                load_validation_logits_artifact(artifact_path)
        finally:
            if artifact_path.exists():
                artifact_path.unlink()

    def test_pipeline_rejects_missing_image_path(self) -> None:
        from food_ai.vision.pipeline import infer_food_image

        with self.assertRaises(FileNotFoundError):
            infer_food_image("food_ai/tests/does_not_exist.jpg", local_files_only=True)
    def test_text_embedding_cache_reuses_saved_embeddings(self) -> None:
        import numpy as np

        from food_ai.vision.cache import TextEmbeddingCache

        class StubEmbedder:
            model_name = "stub-clip"

            def __init__(self) -> None:
                self.calls = 0

            def encode_texts(self, texts: list[str]):
                self.calls += 1
                return np.asarray([[float(index + 1), 0.0] for index, _ in enumerate(texts)], dtype=np.float32)

        cache_dir = WORKSPACE_TEMP_DIR / f"cache_{uuid4().hex}"
        embedder = StubEmbedder()
        cache = TextEmbeddingCache(cache_dir=cache_dir)
        texts = ["a photo of pizza", "a photo of tacos"]
        try:
            first = cache.encode_texts(embedder=embedder, texts=texts, namespace="test_prompts", enable_cache=True)
            second = cache.encode_texts(embedder=embedder, texts=texts, namespace="test_prompts", enable_cache=True)

            self.assertEqual(embedder.calls, 1)
            self.assertTrue(np.array_equal(first, second))
        finally:
            if cache_dir.exists():
                shutil.rmtree(cache_dir)

    def test_pipeline_persists_run_report_and_metadata(self) -> None:
        import csv

        from food_ai.vision.pipeline import infer_food_image

        class StubEmbedder:
            model_name = "stub-clip"

            @staticmethod
            def encode_image(_image_path: str):
                import numpy as np

                return np.asarray([1.0, 0.0], dtype=np.float32)

            @staticmethod
            def build_prompt(text: str) -> str:
                return text

            @staticmethod
            def encode_texts(texts: list[str]):
                import numpy as np

                vectors = []
                for text in texts:
                    lowered = text.lower()
                    if "pizza" in lowered:
                        vector = np.asarray([1.0, 0.0], dtype=np.float32)
                    else:
                        vector = np.asarray([0.1, 0.9], dtype=np.float32)
                    vectors.append(vector / np.linalg.norm(vector))
                return np.vstack(vectors)

        recipe_csv_path = WORKSPACE_TEMP_DIR / f"pipeline_report_{uuid4().hex}.csv"
        report_dir = WORKSPACE_TEMP_DIR / f"reports_{uuid4().hex}"
        try:
            with recipe_csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["title", "ingredients", "directions", "link", "source", "NER"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "title": "Simple Pizza",
                        "ingredients": '["pizza dough", "tomato sauce", "cheese"]',
                        "directions": '["Bake pizza"]',
                        "link": "https://example.com/pizza",
                        "source": "test",
                        "NER": '["pizza dough", "tomato sauce", "cheese"]',
                    }
                )

            result = infer_food_image(
                image_path="food_ai/tests/food_1.jpg",
                recipe_data_path=recipe_csv_path,
                embedder=StubEmbedder(),
                local_files_only=True,
                top_k_dishes=3,
                top_k_recipes=2,
                top_k_ingredients=4,
                save_run_report=True,
                run_report_dir=report_dir,
            )

            self.assertIn("inference_id", result)
            self.assertIn("metadata", result)
            self.assertIn("timings_ms", result["metadata"])
            self.assertIn("config_fingerprint", result["metadata"])
            self.assertTrue(Path(result["metadata"]["run_report_path"]).exists())
            self.assertGreater(result["metadata"]["total_duration_ms"], 0.0)
        finally:
            if recipe_csv_path.exists():
                recipe_csv_path.unlink()
            if report_dir.exists():
                shutil.rmtree(report_dir)

class FoodVisionUITests(unittest.TestCase):
    def test_format_result_for_ui_summarizes_pipeline_output(self) -> None:
        from food_ai.ui.services.formatters import format_result_for_ui

        result = {
            "dish_fused_topk": [{"name": "pizza", "score": 0.82, "source": "classifier+retrieval"}],
            "dish_classifier_topk": [{"name": "pizza", "score": 0.79, "source": "classifier"}],
            "retrieval_candidates": [{"name": "pizza", "score": 0.76, "source": "clip"}],
            "dish_classifier_calibrated": True,
            "dish_classifier_temperature": 1.3,
            "visible_ingredients": [{"ingredient": "cheese", "confidence": 0.7, "visibility_label": "visible", "recipe_hits": 2}],
            "ingredients_ranked": [
                {"ingredient": "cheese", "confidence": 0.7, "visibility_label": "visible", "recipe_hits": 2},
                {"ingredient": "olive oil", "confidence": 0.2, "visibility_label": "hidden_likely", "recipe_hits": 1},
            ],
            "candidate_recipes": [{"title": "Simple Pizza", "score": 0.91, "matched_dish": "pizza", "title_score": 0.8, "image_score": 0.7}],
            "palm_ready": {"status": "ready", "estimated_nutrition_per_100g": {"calories": 210, "protein_g": 9, "fat_g": 8, "carbs_g": 24}},
            "confidence": {"overall": 0.82},
            "warnings": ["classifier_missing"],
            "dish_fusion": {"fallbacks_used": ["classifier_missing"]},
            "segmentation": {"adapter": "none", "hints": []},
            "metadata": {"inference_id": "abc123"},
            "debug": {},
        }

        formatted = format_result_for_ui(result)

        self.assertEqual(formatted["summary"]["top_dish"], "pizza")
        self.assertEqual(formatted["summary"]["ready_state"], "ready")
        self.assertEqual(formatted["dishes"]["classifier_calibrated"], True)
        self.assertEqual(len(formatted["ingredients"]["hidden"]), 1)

    def test_save_test_log_writes_json_record(self) -> None:
        import json

        from food_ai.ui.services.log_service import save_test_log

        logs_dir = WORKSPACE_TEMP_DIR / f"ui_logs_{uuid4().hex}"
        try:
            record_path = save_test_log(
                image_path="food_ai/tests/food_1.jpg",
                inference_result={"dish_fused_topk": [{"name": "pizza"}]},
                notes="tester note",
                review_status="correct",
                logs_dir=logs_dir,
            )
            payload = json.loads(record_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["notes"], "tester note")
            self.assertEqual(payload["review_status"], "correct")
            self.assertIn("dish_fused_topk", payload["inference_result"])
            self.assertTrue(Path(payload["image_path"]).exists())
        finally:
            if logs_dir.exists():
                shutil.rmtree(logs_dir)

class RuntimeAndUsdaHardeningTests(unittest.TestCase):
    def test_runtime_diagnostics_reports_expected_env_mismatch(self) -> None:
        from unittest.mock import patch

        from food_ai.runtime_diagnostics import get_runtime_diagnostics

        with patch.dict('os.environ', {'CONDA_DEFAULT_ENV': 'base'}, clear=False), \
             patch('food_ai.runtime_diagnostics._detect_torch', return_value={'available': True, 'cuda_available': False, 'device_name': None, 'version': '2.2', 'error': None}), \
             patch('food_ai.runtime_diagnostics._detect_import', side_effect=[{'available': True, 'version': '2.5', 'error': None}, {'available': True, 'version': '4.0', 'error': None}]):
            diagnostics = get_runtime_diagnostics(expected_env_name='palm311')

        self.assertFalse(diagnostics['expected_env_match'])
        self.assertIn('unexpected_runtime_environment', diagnostics['warnings'])
        self.assertIn('cuda_unavailable', diagnostics['warnings'])

    def test_load_usda_embedder_reports_deterministic_fallback_reason(self) -> None:
        from unittest.mock import patch

        from food_ai.usda.embeddings import load_usda_embedder

        load_usda_embedder.cache_clear()
        try:
            with patch('food_ai.usda.embeddings._import_sentence_transformer', return_value=None):
                backend = load_usda_embedder(model_name='all-MiniLM-L6-v2', local_files_only=True)
        finally:
            load_usda_embedder.cache_clear()

        self.assertEqual(backend['backend'], 'deterministic_fallback')
        self.assertTrue(backend['fallback_triggered'])
        self.assertIn('sentence_transformers', backend['fallback_reason'])

    def test_load_usda_embedder_selects_cpu_sentence_transformer_backend(self) -> None:
        from unittest.mock import patch

        from food_ai.usda.embeddings import load_usda_embedder

        class FakeCuda:
            @staticmethod
            def is_available() -> bool:
                return False

        class FakeTorch:
            cuda = FakeCuda()

        class FakeSentenceTransformer:
            def __init__(self, model_name: str, **kwargs) -> None:
                self.model_name = model_name
                self.kwargs = kwargs

            def encode(self, texts, **_kwargs):
                import numpy as np

                return np.ones((len(texts), 4), dtype=np.float32)

        load_usda_embedder.cache_clear()
        try:
            with patch('food_ai.usda.embeddings._import_sentence_transformer', return_value=FakeSentenceTransformer), \
                 patch('food_ai.usda.embeddings._import_torch', return_value=FakeTorch()):
                backend = load_usda_embedder(model_name='all-MiniLM-L6-v2', local_files_only=True)
        finally:
            load_usda_embedder.cache_clear()

        self.assertEqual(backend['backend'], 'sentence_transformers_cpu')
        self.assertFalse(backend['fallback_triggered'])
        self.assertEqual(backend['device'], 'cpu')

    def test_normalize_ingredient_with_metadata_tracks_alias_mapping(self) -> None:
        from food_ai.utils.ingredient_normalize import normalize_ingredient_with_metadata

        metadata = normalize_ingredient_with_metadata('1 large diced green pepper')

        self.assertEqual(metadata['cleaned'], 'green pepper')
        self.assertEqual(metadata['canonical'], 'bell pepper')
        self.assertEqual(metadata['normalization_status'], 'normalized')
        self.assertTrue(str(metadata['alias_applied']).endswith('bell pepper'))

    def test_match_ingredient_to_usda_returns_explicit_metadata(self) -> None:
        import csv
        from unittest.mock import patch

        from food_ai.usda.service import get_usda_food_catalog, match_ingredient_to_usda

        csv_path = WORKSPACE_TEMP_DIR / f'usda_match_{uuid4().hex}.csv'
        try:
            with csv_path.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=['fdc_id', 'description', 'category_name'])
                writer.writeheader()
                writer.writerow({'fdc_id': 10, 'description': 'Peppers, sweet, green, raw', 'category_name': 'Vegetables'})

            get_usda_food_catalog.cache_clear()
            with patch('food_ai.usda.service.load_usda_embedder', return_value={'backend': 'deterministic_fallback', 'fallback_reason': 'sentence_transformers import failed'}):
                result = match_ingredient_to_usda('1 large diced green pepper', csv_path=csv_path)
        finally:
            get_usda_food_catalog.cache_clear()
            if csv_path.exists():
                csv_path.unlink()

        self.assertEqual(result['canonical'], 'bell pepper')
        self.assertIn(result['usda_match_method'], {'normalized_bank', 'lexical'})
        self.assertEqual(result['embedding_backend'], 'deterministic_fallback')
        self.assertEqual(result['usda_match_id'], 10)
        self.assertTrue(result['usda_match_label'])

    def test_pipeline_includes_runtime_and_usda_debug_metadata(self) -> None:
        import csv
        from unittest.mock import patch

        from food_ai.vision.pipeline import infer_food_image

        class StubEmbedder:
            model_name = 'stub-clip'

            @staticmethod
            def encode_image(_image_path: str):
                import numpy as np

                return np.asarray([1.0, 0.0], dtype=np.float32)

            @staticmethod
            def build_prompt(text: str) -> str:
                return text

            @staticmethod
            def encode_texts(texts: list[str]):
                import numpy as np

                vectors = []
                for text in texts:
                    vectors.append(np.asarray([1.0, 0.0], dtype=np.float32) if 'pizza' in text.lower() else np.asarray([0.0, 1.0], dtype=np.float32))
                normalized = [vector / np.linalg.norm(vector) for vector in vectors]
                return np.vstack(normalized)

        class StubClassifier:
            def predict_distribution(self, _image_path: str, top_k: int = 5, **_kwargs):
                from food_ai.vision.dish_retrieval import DishCandidate

                candidates = [
                    DishCandidate(name='pizza', label='pizza', score=0.61, prompt='classifier:pizza', source='classifier', component_scores={'classifier': 0.61}),
                    DishCandidate(name='salad', label='salad', score=0.21, prompt='classifier:salad', source='classifier', component_scores={'classifier': 0.21}),
                ]
                return {
                    'top_k_candidates': candidates[:top_k],
                    'probabilities': {'pizza': 0.61, 'salad': 0.21, 'tacos': 0.18},
                    'raw_probabilities': {'pizza': 0.59, 'salad': 0.22, 'tacos': 0.19},
                    'calibrated_probabilities': None,
                    'calibrated': False,
                    'temperature': None,
                    'calibration_artifact_path': None,
                    'logits': [1.0, 0.1, -0.2],
                }

        recipe_csv_path = WORKSPACE_TEMP_DIR / f'pipeline_runtime_{uuid4().hex}.csv'
        try:
            with recipe_csv_path.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=['title', 'ingredients', 'directions', 'link', 'source', 'NER'])
                writer.writeheader()
                writer.writerow({
                    'title': 'Simple Pizza',
                    'ingredients': '["pizza dough", "green pepper", "cheese"]',
                    'directions': '["Bake pizza"]',
                    'link': 'https://example.com/pizza',
                    'source': 'test',
                    'NER': '["pizza dough", "green pepper", "cheese"]',
                })

            with patch('food_ai.vision.pipeline.get_runtime_diagnostics', return_value={
                'python_version': '3.13.11',
                'sys_executable': 'C:/Users/dsarr/miniconda3/python.exe',
                'conda_env': 'base',
                'expected_env_name': 'palm311',
                'expected_env_match': False,
                'torch': {'available': False, 'cuda_available': False, 'device_name': None, 'version': None, 'error': 'missing'},
                'sentence_transformers': {'available': False, 'version': None, 'error': 'missing'},
                'transformers': {'available': False, 'version': None, 'error': 'missing'},
                'warnings': ['unexpected_runtime_environment', 'torch_unavailable'],
                'degraded': True,
            }), \
            patch('food_ai.vision.pipeline.load_usda_embedder', return_value={
                'backend': 'deterministic_fallback',
                'device': 'cpu',
                'model_name': 'hash-token-64d-v1',
                'fallback_triggered': True,
                'fallback_reason': 'sentence_transformers import failed',
            }), \
            patch('food_ai.vision.pipeline.inspect_ingredient_matches', return_value=[{
                'raw_ingredient': 'green pepper',
                'canonical': 'bell pepper',
                'usda_match_label': 'Peppers, sweet, green, raw',
                'usda_match_method': 'normalized_bank',
                'embedding_backend': 'deterministic_fallback',
                'confidence': 1.0,
            }]):
                result = infer_food_image(
                    image_path='food_ai/tests/food_1.jpg',
                    recipe_data_path=recipe_csv_path,
                    embedder=StubEmbedder(),
                    classifier_predictor=StubClassifier(),
                    expected_env_name='palm311',
                    local_files_only=True,
                    top_k_dishes=3,
                    top_k_recipes=1,
                    top_k_ingredients=3,
                )
        finally:
            if recipe_csv_path.exists():
                recipe_csv_path.unlink()

        self.assertIn('runtime_diagnostics', result)
        self.assertIn('usda_status', result)
        self.assertIn('ingredient_normalization', result)
        self.assertIn('unexpected_runtime_environment', result['warnings'])
        self.assertIn('usda_embedding_fallback', result['warnings'])
        self.assertEqual(result['usda_status']['embedding_backend'], 'deterministic_fallback')
        self.assertEqual(result['ingredient_normalization'][0]['canonical'], 'bell pepper')


class FocusedHardeningPassTests(unittest.TestCase):
    def test_normalize_ingredient_with_metadata_tracks_multi_fragment_components(self) -> None:
        from food_ai.utils.ingredient_normalize import normalize_ingredient_with_metadata

        metadata = normalize_ingredient_with_metadata('salt and pepper to taste')

        self.assertEqual(metadata['normalization_status'], 'normalized_multi_fragment')
        self.assertEqual(metadata['canonical'], 'salt; pepper')
        self.assertEqual([component['canonical'] for component in metadata['components']], ['salt', 'pepper'])

    def test_match_ingredient_to_usda_returns_component_matches_for_multi_fragment_line(self) -> None:
        import csv

        from food_ai.usda.service import get_usda_food_catalog, match_ingredient_to_usda

        csv_path = WORKSPACE_TEMP_DIR / f'multi_fragment_usda_{uuid4().hex}.csv'
        try:
            with csv_path.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=['fdc_id', 'description', 'category_name'])
                writer.writeheader()
                writer.writerow({'fdc_id': 10, 'description': 'Salt, table', 'category_name': 'Spices and Herbs'})
                writer.writerow({'fdc_id': 11, 'description': 'Spices, black pepper', 'category_name': 'Spices and Herbs'})

            get_usda_food_catalog.cache_clear()
            result = match_ingredient_to_usda('salt and pepper to taste', csv_path=csv_path, local_files_only=True)
        finally:
            get_usda_food_catalog.cache_clear()
            if csv_path.exists():
                csv_path.unlink()

        self.assertEqual(result['usda_match_method'], 'multi_fragment')
        self.assertIn(result['normalization_status'], {'matched_multi_fragment', 'partial_multi_fragment_match'})
        self.assertEqual(len(result['component_matches']), 2)
        self.assertTrue(any(component.get('usda_match_id') is not None for component in result['component_matches']))

    def test_pipeline_metadata_keeps_classifier_asset_gap_explicit(self) -> None:
        import csv

        from food_ai.vision.pipeline import infer_food_image

        class StubEmbedder:
            model_name = 'stub-clip'

            @staticmethod
            def encode_image(_image_path: str):
                import numpy as np

                return np.asarray([1.0, 0.0], dtype=np.float32)

            @staticmethod
            def build_prompt(text: str) -> str:
                return text

            @staticmethod
            def encode_texts(texts: list[str]):
                import numpy as np

                vectors = []
                for text in texts:
                    lowered = text.lower()
                    vector = np.asarray([1.0, 0.0], dtype=np.float32) if 'pizza' in lowered else np.asarray([0.0, 1.0], dtype=np.float32)
                    vectors.append(vector / np.linalg.norm(vector))
                return np.vstack(vectors)

        recipe_csv_path = WORKSPACE_TEMP_DIR / f'pipeline_asset_gap_{uuid4().hex}.csv'
        try:
            with recipe_csv_path.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=['title', 'ingredients', 'directions', 'link', 'source', 'NER'])
                writer.writeheader()
                writer.writerow({
                    'title': 'Simple Pizza',
                    'ingredients': '["pizza dough", "tomato sauce", "cheese"]',
                    'directions': '["Bake pizza"]',
                    'link': 'https://example.com/pizza',
                    'source': 'test',
                    'NER': '["pizza dough", "tomato sauce", "cheese"]',
                })

            result = infer_food_image(
                image_path='food_ai/tests/food_1.jpg',
                recipe_data_path=recipe_csv_path,
                embedder=StubEmbedder(),
                local_files_only=True,
                top_k_dishes=3,
                top_k_recipes=1,
                top_k_ingredients=3,
            )
        finally:
            if recipe_csv_path.exists():
                recipe_csv_path.unlink()

        self.assertFalse(result['metadata']['classifier_checkpoint_exists'])
        self.assertFalse(result['metadata']['calibration_artifact_exists'])
        self.assertEqual(result['metadata']['inference_mode'], 'retrieval_fallback')
        self.assertIn('classifier_missing', result['warnings'])


if __name__ == "__main__":
    unittest.main()




