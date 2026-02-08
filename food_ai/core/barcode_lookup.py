# -*- coding: utf-8 -*-
"""
Created on Sun Feb  8 11:35:55 2026

@author: dsarrge

PALM Food AI
Offline-first barcode lookup

Flow:
1. Scan / receive barcode
2. Look up locally in SQLite
3. If missing:
   - record unknown barcode
   - (optional) fetch from OpenFoodFacts
   - store normalized data
4. Return nutrition per 100g
"""

import sqlite3
from typing import Optional
from pathlib import Path

from food_ai.utils.openfood_lookup import fetch_product
from food_ai.utils.openfood_store import store_openfood  # will be v2-compatible
# from utils.barcode_read import read_barcode  # optional camera input

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "shared" / "palm.db"

# Local DB lookup
def lookup_local(barcode: str) -> Optional[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            p.product_name,
            p.brand,
            nf.calories,
            nf.protein_g,
            nf.total_fat_g,
            nf.total_carbs_g,
            nf.fiber_g,
            nf.total_sugars_g,
            nf.added_sugars_g,
            nf.sodium_mg,
            nf.cholesterol_mg
        FROM barcodes b
        JOIN products p ON b.product_id = p.product_id
        JOIN nutrition_facts nf ON p.product_id = nf.product_id
        WHERE b.barcode = ?
    """, (barcode,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return dict(row)

# Unknown barcode handler
def record_unknown(barcode: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO unknown_barcodes (barcode)
        VALUES (?)
    """, (barcode,))
    conn.commit()
    conn.close()

# Public entry point
def lookup_barcode(barcode: str, allow_online: bool = True) -> dict:
    """
    Returns nutrition dict (per 100g).
    Raises ValueError if not found and online disabled.
    """
    # Try offline first
    local = lookup_local(barcode)
    if local:
        local["source"] = "local"
        return local

    # Record unknown
    record_unknown(barcode)

    if not allow_online:
        raise ValueError(f"Barcode {barcode} not found offline")

    # Online enrichment
    product = fetch_product(barcode)
    if not product:
        raise ValueError(f"Barcode {barcode} not found online")

    # Store locally (normalized)
    store_openfood(DB_PATH, product)

    # Re-query local DB
    local = lookup_local(barcode)
    if not local:
        raise RuntimeError("Barcode not available offline and online lookup failed")

    local["source"] = "openfoodfacts"
    return local

# CLI / manual test
if __name__ == "__main__":
    while True:
        barcode = input("Scan barcode (or ENTER to quit): ").strip()
        if not barcode:
            break

        try:
            result = lookup_barcode(barcode)
            print("\nFOUND:")
            for k, v in result.items():
                print(f"{k}: {v}")
            print("-" * 30)
        except Exception as e:
            print("ERROR:", e)
