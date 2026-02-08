# -*- coding: utf-8 -*-
"""
Created on Mon Dec 29 17:50:48 2025

@author: dsarr

utils/openfood_store.py

Store OpenFoodFacts product into normalized SQLite schema
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from food_ai.core.nutrition_engine import normalize_nutrition

def store_openfood(db_path: Path, product: dict):
    """
    Persist OpenFoodFacts product into:
    - products (core metadata + raw nutrition blob)
    - nutrition_facts (macros)
    - nutrition_micros (micronutrients)
    """

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Insert / update product
    cur.execute("""
        INSERT OR IGNORE INTO products (
            brand,
            product_name,
            category,
            source
        )
        VALUES (?, ?, ?, ?)
    """, (
        product.get("brand"),
        product.get("product_name"),
        None,
        "OpenFoodFacts"
    ))
    
    # Retrieve product_id
    cur.execute("""
        SELECT product_id
        FROM products
        WHERE product_name = ? AND brand = ?
    """, (
        product.get("product_name"),
        product.get("brand")
    ))
    
    product_id = cur.fetchone()[0]

    # Ensure barcode is linked to product
    cur.execute("""
        INSERT OR IGNORE INTO barcodes (
            barcode,
            product_id,
            format,
            verified
        )
        VALUES (?, ?, ?, ?)
    """, (
        product.get("barcode"),
        product_id,
        None,
        1
    ))

    cur.execute("""
        UPDATE products
        SET
            brand = ?,
            product_name = ?,
            category = ?,
            source = ?,
            raw_nutrition_json = ?,
            updated_at = ?
        WHERE product_id = ?
    """, (
        product.get("brand"),
        product.get("product_name"),
        None,  # category (future)
        "OpenFoodFacts",
        json.dumps(product.get("nutrition", {})),
        datetime.utcnow().isoformat(),
        product_id
    ))

    # Normalize nutrition
    nutrition = normalize_nutrition(product["nutrition"])

    macros = nutrition["macros"]
    micros = nutrition["micros"]

    # Insert macros
    cur.execute("""
        INSERT OR REPLACE INTO nutrition_facts (
            product_id,
            calories,
            total_fat_g,
            saturated_fat_g,
            trans_fat_g,
            total_carbs_g,
            fiber_g,
            total_sugars_g,
            added_sugars_g,
            protein_g,
            sodium_mg,
            cholesterol_mg
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        product_id,
        macros.get("calories"),
        macros.get("total_fat_g"),
        macros.get("saturated_fat_g"),
        macros.get("trans_fat_g"),
        macros.get("total_carbs_g"),
        macros.get("fiber_g"),
        macros.get("total_sugars_g"),
        macros.get("added_sugars_g"),
        macros.get("protein_g"),
        macros.get("sodium_mg"),
        macros.get("cholesterol_mg")
    ))

    # Insert micronutrients
    cur.execute("""
        INSERT OR REPLACE INTO nutrition_micros (
            product_id,
            vitamin_d_mcg,
            calcium_mg,
            iron_mg,
            potassium_mg,
            caffeine_mg
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        product_id,
        micros.get("vitamin_d_mcg"),
        micros.get("calcium_mg"),
        micros.get("iron_mg"),
        micros.get("potassium_mg"),
        micros.get("caffeine_mg")
    ))

    cur.execute(
        "SELECT 1 FROM products WHERE product_id = ?",
        (product_id,)
    )
    if cur.fetchone() is None:
        raise RuntimeError(f"product_id {product_id} does not exist")

    conn.commit()
    conn.close()
