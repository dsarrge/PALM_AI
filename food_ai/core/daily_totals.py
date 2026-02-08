# -*- coding: utf-8 -*-
"""
Created on Sun Feb  8 12:12:43 2026

@author: dsarr

PALM Food AI
Daily nutrition totals
food_ai/core/daily_totals.py
"""
from datetime import date
from typing import Dict
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BASE_DIR / "shared" / "palm.db"


def fetch_consumption_for_day(day: date):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            c.barcode,
            c.grams_consumed,
            c.consumed_at,
            nf.calories,
            nf.protein_g,
            nf.total_fat_g,
            nf.total_carbs_g,
            nf.fiber_g,
            nf.total_sugars_g,
            nf.added_sugars_g,
            nf.sodium_mg,
            nf.cholesterol_mg
        FROM consumption_logs c
        JOIN barcodes b ON c.barcode = b.barcode
        JOIN nutrition_facts nf ON b.product_id = nf.product_id
        WHERE DATE(c.consumed_at) = ?
    """, (day.isoformat(),))

    rows = cur.fetchall()
    conn.close()
    return rows


def aggregate_daily_totals(rows) -> Dict:
    totals = {
        "calories": 0.0,
        "protein_g": 0.0,
        "total_fat_g": 0.0,
        "total_carbs_g": 0.0,
        "fiber_g": 0.0,
        "total_sugars_g": 0.0,
        "added_sugars_g": 0.0,
        "sodium_mg": 0.0,
        "cholesterol_mg": 0.0,
        "items_logged": 0
    }

    for r in rows:
        factor = r["grams_consumed"] / 100.0

        for k in (
            "calories",
            "protein_g",
            "total_fat_g",
            "total_carbs_g",
            "fiber_g",
            "total_sugars_g",
            "added_sugars_g",
            "sodium_mg",
            "cholesterol_mg"
        ):
            v = r[k]
            if v is not None:
                totals[k] += v * factor

        totals["items_logged"] += 1

    return totals


def daily_totals(day: date | None = None, db_path=None):
    if day is None:
        day = date.today()

    rows = fetch_consumption_for_day(day)
    totals = aggregate_daily_totals(rows)
    totals["date"] = day.isoformat()
    return totals


if __name__ == "__main__":
    totals = daily_totals()

    print(f"\nDAILY TOTALS ({totals['date']})")
    print("-" * 30)
    print(f"Calories: {round(totals['calories'])}")
    print(f"Protein:  {round(totals['protein_g'], 1)} g")
    print(f"Fat:      {round(totals['total_fat_g'], 1)} g")
    print(f"Carbs:    {round(totals['total_carbs_g'], 1)} g")
    print(f"Fiber:    {round(totals['fiber_g'], 1)} g")
    print(f"Sugar:    {round(totals['total_sugars_g'], 1)} g")
    print(f"Sodium:   {round(totals['sodium_mg'])} mg")
    print(f"Items:    {totals['items_logged']}")
    print("-" * 30)
