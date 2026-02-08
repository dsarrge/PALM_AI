# -*- coding: utf-8 -*-
"""
Created on Sun Feb  8 11:54:02 2026

@author: dsarr

PALM Food AI
Log food consumption (barcode + grams)
food_ai/core/log_consumption.py
"""
# -*- coding: utf-8 -*-
"""
PALM Food AI
Log food consumption (barcode + grams)
"""

import sqlite3
from datetime import datetime

from food_ai.core.barcode_lookup import lookup_barcode
from food_ai.core.nutrition_engine import normalize_nutrition

# Intake scaling
def scale_intake(per_100g: dict, grams: float) -> dict:
    factor = grams / 100.0
    return {
        k: (v * factor if v is not None else None)
        for k, v in per_100g.items()
    }

# Intake validation
def validate_intake(intake: dict) -> dict:
    flags = []

    for k, v in intake.items():
        if v is None:
            flags.append(f"missing_{k}")
        elif v < 0:
            flags.append(f"negative_{k}")

    confidence = 1.0
    if flags:
        confidence -= 0.3

    return {
        "valid": confidence > 0.5,
        "confidence": max(0.0, confidence),
        "flags": flags
    }

# Log consumption
def log_consumption(barcode: str, grams: float, db_path):
    if grams <= 0:
        raise ValueError("grams must be > 0")

    # Lookup product (offline-first)
    product = lookup_barcode(barcode)

    per_100g = {
        k: (product.get(k) if product.get(k) is not None else 0.0)
        for k in (
            "calories",
            "protein_g",
            "total_fat_g",
            "total_carbs_g",
            "fiber_g",
            "total_sugars_g",
            "sodium_mg",
            "cholesterol_mg",
        )
    }

    # Normalize + validate base nutrition
    normalized = normalize_nutrition(per_100g)

    # Scale to actual intake
    intake = scale_intake(normalized["per_100g"], grams)

    # Validate scaled intake
    validation = validate_intake(intake)

    # Store consumption event
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO consumption_logs (
            barcode,
            grams_consumed,
            consumed_at
        ) VALUES (?, ?, ?)
    """, (
        barcode,
        grams,
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()

    return {
        "barcode": barcode,
        "grams": grams,
        "intake": intake,
        "validation": validation,
        "nutrition_quality": normalized["quality"],
        "source": product.get("source")
    }

# CLI test
if __name__ == "__main__":
    while True:
        barcode = input("Barcode (ENTER to quit): ").strip()
        if not barcode:
            break

        grams = float(input("Grams consumed: "))
        result = log_consumption(barcode, grams)

        print("\nLOGGED:")
        for k, v in result["intake"].items():
            print(f"{k}: {round(v, 2) if v is not None else 'NA'}")

        print("confidence:", result["validation"]["confidence"])
        if result["validation"]["flags"]:
            print("flags:", result["validation"]["flags"])
        print("-" * 30)
