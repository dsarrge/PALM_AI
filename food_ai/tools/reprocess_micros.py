# -*- coding: utf-8 -*-
"""
Created on Sun Feb  8 13:52:37 2026

@author: dsarr
food_ai/tools/reprocess_micros.py
"""

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "shared" / "palm.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
    SELECT product_id, raw_nutrition_json
    FROM products
    WHERE raw_nutrition_json IS NOT NULL
""")

rows = cur.fetchall()

for r in rows:
    nutrition = json.loads(r["raw_nutrition_json"])

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
        r["product_id"],
        nutrition.get("vitamin_d_mcg"),
        nutrition.get("calcium_mg"),
        nutrition.get("iron_mg"),
        nutrition.get("potassium_mg"),
        nutrition.get("caffeine_mg")
    ))

conn.commit()
conn.close()

print("Micronutrients reprocessed successfully.")
