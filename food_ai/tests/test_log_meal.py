from __future__ import annotations

from contextlib import closing
import os
import sqlite3
import tempfile
import unittest

from food_ai.barcode.service import lookup_local_barcode, record_unknown_barcode
from food_ai.core.daily_totals import get_daily_totals
from food_ai.core.log_consumption import log_consumption
from food_ai.utils.openfood_store import store_openfood


def _new_temp_db() -> str:
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    return path


def _create_catalog_tables(connection: sqlite3.Connection) -> None:
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
        """
    )


def _create_store_and_log_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE serving_sizes (
            serving_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            serving_name TEXT,
            grams REAL NOT NULL,
            is_default INTEGER DEFAULT 0
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
        CREATE TABLE consumption_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            ref_id TEXT,
            grams_consumed REAL NOT NULL,
            consumed_at TEXT NOT NULL,
            calories REAL,
            protein_g REAL,
            total_fat_g REAL,
            total_carbs_g REAL
        );
        """
    )


def _insert_sample_product(
    connection: sqlite3.Connection,
    barcode: str,
    *,
    calories: float = 200.0,
    protein_g: float = 10.0,
    total_fat_g: float = 5.0,
    total_carbs_g: float = 20.0,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO products (brand, product_name, category, source, raw_nutrition_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("PALM", "Sample Product", "Bars", "test", "{}"),
    )
    product_id = int(cursor.lastrowid)
    connection.execute(
        "INSERT INTO barcodes (barcode, product_id, format, verified) VALUES (?, ?, ?, ?)",
        (barcode, product_id, "ean13", 1),
    )
    connection.execute(
        """
        INSERT INTO nutrition_facts (
            product_id,
            calories,
            protein_g,
            total_fat_g,
            total_carbs_g
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (product_id, calories, protein_g, total_fat_g, total_carbs_g),
    )
    return product_id


class BarcodePersistenceCorrectnessTests(unittest.TestCase):
    def test_duplicate_store_same_product_is_idempotent_across_tables(self) -> None:
        db_path = _new_temp_db()
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                _create_catalog_tables(connection)
                _create_store_and_log_tables(connection)
                connection.commit()

            product = {
                "barcode": "7777777777777",
                "product_name": "Double Store Bar",
                "brand": "PALM",
                "category": "Bars",
                "source": "openfoodfacts",
                "format": "ean13",
                "verified": 0,
                "nutrition": {
                    "calories": 250.0,
                    "protein_g": 12.0,
                    "total_fat_g": 8.0,
                    "total_carbs_g": 30.0,
                },
                "serving_sizes": [
                    {"serving_name": "1 bar (40 g)", "grams": 40.0, "is_default": 1},
                    {"serving_name": "2 bars (80 g)", "grams": 80.0, "is_default": 0},
                ],
            }

            first_id = store_openfood(db_path, product)
            second_id = store_openfood(db_path, product)
            self.assertEqual(first_id, second_id)

            with closing(sqlite3.connect(db_path)) as connection:
                product_count = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
                barcode_count = connection.execute("SELECT COUNT(*) FROM barcodes").fetchone()[0]
                nutrition_count = connection.execute("SELECT COUNT(*) FROM nutrition_facts").fetchone()[0]
                serving_count = connection.execute(
                    "SELECT COUNT(*) FROM serving_sizes WHERE product_id = ?",
                    (first_id,),
                ).fetchone()[0]

            self.assertEqual(product_count, 1)
            self.assertEqual(barcode_count, 1)
            self.assertEqual(nutrition_count, 1)
            self.assertEqual(serving_count, 2)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_duplicate_unknown_barcode_insert_remains_single_row(self) -> None:
        db_path = _new_temp_db()
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                _create_catalog_tables(connection)
                _create_store_and_log_tables(connection)
                connection.commit()

            record_unknown_barcode("9999999999990", db_path)
            record_unknown_barcode("9999999999990", db_path)

            with closing(sqlite3.connect(db_path)) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM unknown_barcodes WHERE barcode = ?",
                    ("9999999999990",),
                ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_serving_sizes_persist_and_lookup_returns_default_grams(self) -> None:
        db_path = _new_temp_db()
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                _create_catalog_tables(connection)
                _create_store_and_log_tables(connection)
                connection.commit()

            product = {
                "barcode": "8888888888888",
                "product_name": "Serving Check Bar",
                "brand": "PALM",
                "category": "Bars",
                "source": "openfoodfacts",
                "format": "ean13",
                "verified": 0,
                "nutrition": {
                    "calories": 300.0,
                    "protein_g": 15.0,
                    "total_fat_g": 10.0,
                    "total_carbs_g": 25.0,
                },
                "serving_sizes": [
                    {"serving_name": "half bar", "grams": 30.0, "is_default": 0},
                    {"serving_name": "1 bar", "grams": 60.0, "is_default": 1},
                ],
            }
            store_openfood(db_path, product)

            looked_up = lookup_local_barcode("8888888888888", db_path)
            self.assertIsNotNone(looked_up)
            assert looked_up is not None
            self.assertAlmostEqual(looked_up["serving_size_default_g"], 60.0)
            self.assertAlmostEqual(looked_up["calories"], 300.0)
            self.assertAlmostEqual(looked_up["protein_g"], 15.0)
            self.assertAlmostEqual(looked_up["total_fat_g"], 10.0)
            self.assertAlmostEqual(looked_up["total_carbs_g"], 25.0)

            with closing(sqlite3.connect(db_path)) as connection:
                rows = connection.execute(
                    "SELECT serving_name, grams, is_default FROM serving_sizes ORDER BY serving_id ASC"
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(set(rows), {("half bar", 30.0, 0), ("1 bar", 60.0, 1)})
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_lookup_local_barcode_reads_v2_product_macros_without_nutrition_facts(self) -> None:
        db_path = _new_temp_db()
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE products (
                        product_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        brand TEXT,
                        brand_owner TEXT,
                        brand_name TEXT,
                        product_name TEXT NOT NULL,
                        category TEXT,
                        source TEXT,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        raw_nutrition_json TEXT,
                        calories_per_100g REAL,
                        protein_g_per_100g REAL,
                        total_fat_g_per_100g REAL,
                        total_carbs_g_per_100g REAL
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
                    """
                )
                cursor = connection.execute(
                    """
                    INSERT INTO products (
                        brand,
                        brand_owner,
                        brand_name,
                        product_name,
                        category,
                        source,
                        raw_nutrition_json,
                        calories_per_100g,
                        protein_g_per_100g,
                        total_fat_g_per_100g,
                        total_carbs_g_per_100g
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("PALM", "PALM", "PALM", "Test Pasta Bowl", "Prepared Meals", "usda_branded", "{}", 190.0, 12.0, 6.0, 20.0),
                )
                product_id = int(cursor.lastrowid)
                connection.execute(
                    "INSERT INTO barcodes (barcode, product_id, format, verified) VALUES (?, ?, ?, ?)",
                    ("5555555555555", product_id, "upc", 1),
                )
                connection.execute(
                    "INSERT INTO serving_sizes (product_id, serving_name, grams, is_default) VALUES (?, ?, ?, ?)",
                    (product_id, "1 bowl", 255.0, 1),
                )
                connection.commit()

            looked_up = lookup_local_barcode("5555555555555", db_path)
            self.assertIsNotNone(looked_up)
            assert looked_up is not None
            self.assertEqual(looked_up["product_name"], "Test Pasta Bowl")
            self.assertEqual(looked_up["brand"], "PALM")
            self.assertAlmostEqual(looked_up["calories"], 190.0)
            self.assertAlmostEqual(looked_up["protein_g"], 12.0)
            self.assertAlmostEqual(looked_up["total_fat_g"], 6.0)
            self.assertAlmostEqual(looked_up["total_carbs_g"], 20.0)
            self.assertAlmostEqual(looked_up["serving_size_default_g"], 255.0)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_changed_serving_sizes_refresh_existing_rows(self) -> None:
        db_path = _new_temp_db()
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                _create_catalog_tables(connection)
                _create_store_and_log_tables(connection)
                connection.commit()

            original = {
                "barcode": "9191919191919",
                "product_name": "Refresh Bar",
                "brand": "PALM",
                "category": "Bars",
                "source": "openfoodfacts",
                "format": "ean13",
                "verified": 0,
                "nutrition": {
                    "calories": 200.0,
                    "protein_g": 10.0,
                    "total_fat_g": 5.0,
                    "total_carbs_g": 20.0,
                },
                "serving_sizes": [
                    {"serving_name": "old default", "grams": 40.0, "is_default": 1},
                    {"serving_name": "old alt", "grams": 80.0, "is_default": 0},
                ],
            }
            updated = {
                **original,
                "serving_sizes": [
                    {"serving_name": "new default", "grams": 55.0, "is_default": 1},
                ],
            }

            store_openfood(db_path, original)
            store_openfood(db_path, updated)

            with closing(sqlite3.connect(db_path)) as connection:
                rows = connection.execute(
                    "SELECT serving_name, grams, is_default FROM serving_sizes ORDER BY serving_id ASC"
                ).fetchall()

            self.assertEqual(rows, [("new default", 55.0, 1)])
            looked_up = lookup_local_barcode("9191919191919", db_path)
            self.assertIsNotNone(looked_up)
            assert looked_up is not None
            self.assertAlmostEqual(looked_up["serving_size_default_g"], 55.0)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_missing_or_empty_serving_sizes_does_not_wipe_existing(self) -> None:
        db_path = _new_temp_db()
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                _create_catalog_tables(connection)
                _create_store_and_log_tables(connection)
                connection.commit()

            base = {
                "barcode": "9292929292929",
                "product_name": "Preserve Bar",
                "brand": "PALM",
                "category": "Bars",
                "source": "openfoodfacts",
                "format": "ean13",
                "verified": 0,
                "nutrition": {
                    "calories": 210.0,
                    "protein_g": 11.0,
                    "total_fat_g": 6.0,
                    "total_carbs_g": 22.0,
                },
                "serving_sizes": [
                    {"serving_name": "keep me", "grams": 45.0, "is_default": 1},
                ],
            }
            store_openfood(db_path, base)

            without_servings_key = {
                key: value for key, value in base.items() if key != "serving_sizes"
            }
            with_empty_servings = {**base, "serving_sizes": []}
            store_openfood(db_path, without_servings_key)
            store_openfood(db_path, with_empty_servings)

            with closing(sqlite3.connect(db_path)) as connection:
                rows = connection.execute(
                    "SELECT serving_name, grams, is_default FROM serving_sizes ORDER BY serving_id ASC"
                ).fetchall()

            self.assertEqual(rows, [("keep me", 45.0, 1)])
            looked_up = lookup_local_barcode("9292929292929", db_path)
            self.assertIsNotNone(looked_up)
            assert looked_up is not None
            self.assertAlmostEqual(looked_up["serving_size_default_g"], 45.0)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_nutrition_snapshot_scaling_matches_stored_per_100g(self) -> None:
        db_path = _new_temp_db()
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                _create_catalog_tables(connection)
                _create_store_and_log_tables(connection)
                _insert_sample_product(
                    connection,
                    "1212121212121",
                    calories=320.0,
                    protein_g=16.0,
                    total_fat_g=12.0,
                    total_carbs_g=40.0,
                )
                connection.commit()

            result = log_consumption("1212121212121", 37.5, db_path)
            snapshot = result["nutrition_snapshot"]
            self.assertAlmostEqual(snapshot["calories"], 120.0)
            self.assertAlmostEqual(snapshot["protein_g"], 6.0)
            self.assertAlmostEqual(snapshot["total_fat_g"], 4.5)
            self.assertAlmostEqual(snapshot["total_carbs_g"], 15.0)

            with closing(sqlite3.connect(db_path)) as connection:
                row = connection.execute(
                    "SELECT calories, protein_g, total_fat_g, total_carbs_g FROM consumption_logs WHERE ref_id = ?",
                    ("1212121212121",),
                ).fetchone()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertAlmostEqual(float(row[0]), 120.0)
            self.assertAlmostEqual(float(row[1]), 6.0)
            self.assertAlmostEqual(float(row[2]), 4.5)
            self.assertAlmostEqual(float(row[3]), 15.0)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_multiple_logs_same_day_totals_match_sum_of_entries(self) -> None:
        db_path = _new_temp_db()
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                _create_catalog_tables(connection)
                _create_store_and_log_tables(connection)
                _insert_sample_product(
                    connection,
                    "3434343434343",
                    calories=250.0,
                    protein_g=10.0,
                    total_fat_g=5.0,
                    total_carbs_g=30.0,
                )
                connection.commit()

            first = log_consumption("3434343434343", 20.0, db_path)
            second = log_consumption("3434343434343", 30.0, db_path)
            day = str(first["consumed_at"])[:10]

            expected_calories = first["nutrition_snapshot"]["calories"] + second["nutrition_snapshot"]["calories"]
            expected_protein = first["nutrition_snapshot"]["protein_g"] + second["nutrition_snapshot"]["protein_g"]
            expected_fat = first["nutrition_snapshot"]["total_fat_g"] + second["nutrition_snapshot"]["total_fat_g"]
            expected_carbs = first["nutrition_snapshot"]["total_carbs_g"] + second["nutrition_snapshot"]["total_carbs_g"]

            totals = get_daily_totals(db_path, date=day)
            self.assertAlmostEqual(totals["total_quantity"], 50.0)
            self.assertAlmostEqual(totals["nutrition_totals"]["calories"], expected_calories)
            self.assertAlmostEqual(totals["nutrition_totals"]["protein_g"], expected_protein)
            self.assertAlmostEqual(totals["nutrition_totals"]["total_fat_g"], expected_fat)
            self.assertAlmostEqual(totals["nutrition_totals"]["total_carbs_g"], expected_carbs)
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    def test_log_consumption_records_unknown_barcode(self) -> None:
        db_path = _new_temp_db()
        try:
            with closing(sqlite3.connect(db_path)) as connection:
                _create_catalog_tables(connection)
                _create_store_and_log_tables(connection)
                connection.commit()
            result = log_consumption('999', 1.0, db_path)
            self.assertEqual(result['status'], 'unknown_barcode')
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)


if __name__ == '__main__':
    unittest.main()
