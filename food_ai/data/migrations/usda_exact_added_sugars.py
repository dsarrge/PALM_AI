from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
from typing import Any

from food_ai.config import PALM_V2_DB_PATH

ADDED_SUGAR_NUTRIENT_NBR = "539"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_real_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table) or column in _columns(conn, table):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} REAL")
    return True


def _populate_added_sugars(conn: sqlite3.Connection, table: str, column: str, fdc_column: str) -> int:
    if not (_table_exists(conn, table) and _table_exists(conn, "usda_food_nutrient") and _table_exists(conn, "usda_nutrient")):
        return 0
    if column not in _columns(conn, table) or fdc_column not in _columns(conn, table):
        return 0
    cursor = conn.execute(
        f"""
        UPDATE {table}
        SET {column} = (
            SELECT CAST(fn.amount AS REAL)
            FROM usda_food_nutrient fn
            JOIN usda_nutrient n ON CAST(n.id AS INTEGER) = CAST(fn.nutrient_id AS INTEGER)
            WHERE CAST(fn.fdc_id AS INTEGER) = CAST({table}.{fdc_column} AS INTEGER)
              AND TRIM(CAST(n.nutrient_nbr AS TEXT)) = ?
              AND UPPER(TRIM(CAST(n.unit_name AS TEXT))) = 'G'
              AND fn.amount IS NOT NULL
            LIMIT 1
        )
        WHERE {fdc_column} IS NOT NULL
          AND EXISTS (
            SELECT 1
            FROM usda_food_nutrient fn
            JOIN usda_nutrient n ON CAST(n.id AS INTEGER) = CAST(fn.nutrient_id AS INTEGER)
            WHERE CAST(fn.fdc_id AS INTEGER) = CAST({table}.{fdc_column} AS INTEGER)
              AND TRIM(CAST(n.nutrient_nbr AS TEXT)) = ?
              AND UPPER(TRIM(CAST(n.unit_name AS TEXT))) = 'G'
              AND fn.amount IS NOT NULL
          )
        """,
        (ADDED_SUGAR_NUTRIENT_NBR, ADDED_SUGAR_NUTRIENT_NBR),
    )
    return int(cursor.rowcount or 0)


def apply_added_sugars(db_path: str | Path = PALM_V2_DB_PATH) -> dict[str, Any]:
    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(db)
    with sqlite3.connect(db) as conn:
        added = []
        for table, column in (
            ("canonical_food_nutrients", "added_sugars_g_per_100g"),
            ("products", "added_sugars_g_per_100g"),
            ("consumption_logs", "added_sugars_g"),
            ("meal_log_ingredients", "added_sugars_g"),
            ("meal_logs", "added_sugars_g"),
        ):
            if _add_real_column(conn, table, column):
                added.append(f"{table}.{column}")
        canonical = _populate_added_sugars(conn, "canonical_food_nutrients", "added_sugars_g_per_100g", "source_fdc_id")
        products = _populate_added_sugars(conn, "products", "added_sugars_g_per_100g", "source_fdc_id")
        conn.commit()
    return {"db_path": str(db), "added_columns": added, "canonical_rows_updated": canonical, "product_rows_updated": products}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=PALM_V2_DB_PATH)
    args = parser.parse_args()
    print(apply_added_sugars(args.db))


if __name__ == "__main__":
    main()
