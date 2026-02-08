# -*- coding: utf-8 -*-
"""
Created on Sun Feb  8 12:29:23 2026

@author: dsarr

PALM Food AI
End-to-end pipeline:
Image -> Barcode -> Log -> Daily Totals
food_ai/tools/image_to_log.py
"C:\PALM\food_ai\tests\IMG_2469.jpeg"
"C:\PALM\food_ai\tests\IMG_2470.jpeg"
"""

from pathlib import Path

from food_ai.utils.barcode_read import read_barcode
from food_ai.core.log_consumption import log_consumption
from food_ai.core.daily_totals import daily_totals

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "shared" / "palm.db"

def run_pipeline(image_path: str, grams: float):
    barcode = read_barcode(image_path)
    if not barcode:
        raise RuntimeError("No barcode detected")

    log_consumption(barcode, grams, DB_PATH)
    totals = daily_totals(db_path=DB_PATH)

    return {"daily_totals": totals}

# CLI entry
if __name__ == "__main__":
    image_path = input("Path to barcode image: ").strip().strip('"').strip("'")
    grams = float(input("Grams consumed: "))

    out = run_pipeline(image_path, grams)

    print("\nTODAY SO FAR")
    print("-" * 32)
    for k, v in out["daily_totals"].items():
        if k not in {"date", "items_logged"}:
            print(f"{k}: {round(v, 2)}")
    print(f"Items logged: {out['daily_totals']['items_logged']}")
