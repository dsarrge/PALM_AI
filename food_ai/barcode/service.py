from __future__ import annotations

from contextlib import closing
import json
import sqlite3
from pathlib import Path
from typing import Any

from food_ai.nutrition import normalize_nutrition


def lookup_barcode(barcode: str, db_path: str | Path) -> dict[str, Any] | None:
    with closing(sqlite3.connect(db_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        has_nutrition = "nutrition_facts" in tables
        has_serving_sizes = "serving_sizes" in tables
        product_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(products)").fetchall()
        } if "products" in tables else set()
        has_product_macros = {
            "calories_per_100g",
            "protein_g_per_100g",
            "total_fat_g_per_100g",
            "total_carbs_g_per_100g",
        }.issubset(product_columns)

        serving_select = (
            "(SELECT grams FROM serving_sizes s WHERE s.product_id = p.product_id ORDER BY s.is_default DESC, s.serving_id ASC LIMIT 1)"
            if has_serving_sizes
            else "0"
        )

        if has_nutrition:
            row = connection.execute(
                f"""
                SELECT
                    p.product_id,
                    p.product_name,
                    p.brand,
                    p.category,
                    p.source,
                    b.barcode,
                    nf.calories,
                    nf.protein_g,
                    nf.total_fat_g,
                    nf.total_carbs_g,
                    {serving_select} AS default_serving_grams
                FROM barcodes b
                JOIN products p ON p.product_id = b.product_id
                LEFT JOIN nutrition_facts nf ON nf.product_id = p.product_id
                WHERE b.barcode = ?
                """,
                (barcode,),
            ).fetchone()
        elif has_product_macros:
            row = connection.execute(
                f"""
                SELECT
                    p.product_id,
                    p.product_name,
                    COALESCE(p.brand, p.brand_name, p.brand_owner),
                    p.category,
                    p.source,
                    b.barcode,
                    p.calories_per_100g,
                    p.protein_g_per_100g,
                    p.total_fat_g_per_100g,
                    p.total_carbs_g_per_100g,
                    {serving_select} AS default_serving_grams
                FROM barcodes b
                JOIN products p ON p.product_id = b.product_id
                WHERE b.barcode = ?
                """,
                (barcode,),
            ).fetchone()
        else:
            row = connection.execute(
                f"""
                SELECT
                    p.product_id,
                    p.product_name,
                    p.brand,
                    p.category,
                    p.source,
                    b.barcode,
                    0,
                    0,
                    0,
                    0,
                    {serving_select} AS default_serving_grams
                FROM barcodes b
                JOIN products p ON p.product_id = b.product_id
                WHERE b.barcode = ?
                """,
                (barcode,),
            ).fetchone()

    if row is None:
        return None

    nutrition = {
        "calories": float(row[6] or 0.0),
        "protein_g": float(row[7] or 0.0),
        "total_fat_g": float(row[8] or 0.0),
        "total_carbs_g": float(row[9] or 0.0),
    }
    return {
        "product_id": row[0],
        "product_name": row[1],
        "brand": row[2],
        "category": row[3],
        "source": row[4],
        "barcode": row[5],
        "serving_size_default_g": float(row[10] or 0.0),
        "nutrition": nutrition,
        "calories": nutrition["calories"],
        "protein_g": nutrition["protein_g"],
        "total_fat_g": nutrition["total_fat_g"],
        "total_carbs_g": nutrition["total_carbs_g"],
    }


def lookup_local_barcode(barcode: str, db_path: str | Path) -> dict[str, Any] | None:
    return lookup_barcode(barcode, db_path)


def record_unknown_barcode(barcode: str, db_path: str | Path) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("INSERT OR IGNORE INTO unknown_barcodes (barcode) VALUES (?)", (barcode,))
        connection.commit()


def store_openfood_product(product: dict[str, Any], db_path: str | Path) -> int:
    barcode = str(product["barcode"])
    serving_sizes = product.get("serving_sizes") or []
    normalized = normalize_nutrition(product.get("nutrition") or {})["normalized"]
    with closing(sqlite3.connect(db_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        existing = connection.execute("SELECT product_id FROM barcodes WHERE barcode = ?", (barcode,)).fetchone()
        if existing is not None:
            product_id = int(existing[0])
        else:
            cursor = connection.execute(
                "INSERT INTO products (brand, product_name, category, source, raw_nutrition_json) VALUES (?, ?, ?, ?, ?)",
                (
                    product.get("brand"),
                    product.get("product_name"),
                    product.get("category"),
                    product.get("source", "openfoodfacts"),
                    json.dumps(product.get("nutrition") or {}, sort_keys=True),
                ),
            )
            product_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO barcodes (barcode, product_id, format, verified) VALUES (?, ?, ?, ?)",
                (barcode, product_id, product.get("format", "ean13"), int(bool(product.get("verified", 0)))),
            )

        if "nutrition_facts" in tables:
            connection.execute(
                "INSERT OR REPLACE INTO nutrition_facts (product_id, calories, protein_g, total_fat_g, total_carbs_g) VALUES (?, ?, ?, ?, ?)",
                (product_id, normalized["calories"], normalized["protein_g"], normalized["total_fat_g"], normalized["total_carbs_g"]),
            )
        elif "products" in tables:
            product_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(products)").fetchall()
            }
            if {
                "calories_per_100g",
                "protein_g_per_100g",
                "total_fat_g_per_100g",
                "total_carbs_g_per_100g",
            }.issubset(product_columns):
                connection.execute(
                    """
                    UPDATE products
                    SET calories_per_100g = ?,
                        protein_g_per_100g = ?,
                        total_fat_g_per_100g = ?,
                        total_carbs_g_per_100g = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE product_id = ?
                    """,
                    (
                        normalized["calories"],
                        normalized["protein_g"],
                        normalized["total_fat_g"],
                        normalized["total_carbs_g"],
                        product_id,
                    ),
                )
        if "nutrition_micros" in tables:
            connection.execute("INSERT OR REPLACE INTO nutrition_micros (product_id) VALUES (?)", (product_id,))
        already_has_servings = connection.execute("SELECT COUNT(*) FROM serving_sizes WHERE product_id = ?", (product_id,)).fetchone()[0]
        if not already_has_servings:
            for serving in serving_sizes:
                connection.execute(
                    "INSERT INTO serving_sizes (product_id, serving_name, grams, is_default) VALUES (?, ?, ?, ?)",
                    (product_id, serving.get("serving_name"), float(serving.get("grams", 0.0)), int(bool(serving.get("is_default", 0)))),
                )
        connection.commit()
    return product_id
