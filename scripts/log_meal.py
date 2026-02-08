# -*- coding: utf-8 -*-
"""
Created on Tue Dec 30 12:51:49 2025

@author: dsarr

Food AI – Meal Logging
log_meal.py
"""
# -*- coding: utf-8 -*-
"""
Food AI – Meal Logging
log_meal.py
"""

# PATH + WARNINGS
import sys
sys.path.insert(0, "C:/PALM")

import warnings
warnings.filterwarnings("ignore")

# IMPORTS
from datetime import datetime
from typing import Dict, Optional
from pathlib import Path
import sqlite3
import uuid

# PATHS
BASE_DIR = Path(__file__).resolve().parents[2]   # C:/PALM
DB_PATH = BASE_DIR / "shared" / "palm.db"
SCHEMA_PATH = BASE_DIR / "food_ai" / "sync" / "schema.sql"

# DB INITIALIZATION
def init_db(db_path: Path = DB_PATH):
    """
    Ensure database schema exists.
    Safe to call multiple times.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.close()

# CORE API
"""
Persist a user-confirmed meal to the database.
upload_method: image | label | barcode | manual
Returns: meal_id (int)
"""
def log_meal(
    *,
    upload_method: str,
    inferred_dish: str,
    model_confidence: float,
    final_dish: str,
    ingredients: Dict[str, float],
    nutrition: Dict[str, float],
    image_path: Optional[str] = None,
    barcode: Optional[str] = None,
    model_version: str = "v1",
    db_path: Path = DB_PATH,
) -> int:
    if upload_method not in {"image", "label", "barcode", "manual"}:
        raise ValueError("upload_method must be image, label, barcode, or manual")
    
    meal_uuid = str(uuid.uuid4())
    init_db(db_path)
    user_corrected = int(inferred_dish != final_dish)
    
    ts   = datetime.utcnow().isoformat()
    conn = sqlite3.connect(db_path)
    
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # INSERT CORE MEAL
    cur.execute(
        """
        INSERT INTO meals (
            meal_uuid,
            timestamp_utc,
            upload_method,
            image_path,
            barcode,
            inferred_dish,
            model_confidence,
            final_dish,
            user_corrected,
            model_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
        meal_uuid,
        ts,
        upload_method,
        image_path,
        barcode,
        inferred_dish,
        model_confidence,
        final_dish,
        user_corrected,
        model_version,
        ),)
    
    meal_id = cur.lastrowid

    # INSERT INGREDIENTS
    for ingredient, weight in ingredients.items():
        cur.execute(
            """
            INSERT INTO meal_ingredients (
                meal_id,
                ingredient,
                weight
                ) VALUES (?, ?, ?)
                """, (meal_id, ingredient, float(weight)),)

    # INSERT NUTRITION SNAPSHOT
    cur.execute(
        """
        INSERT INTO meal_nutrition (
            meal_id,
            calories,
            protein_g,
            fat_g,
            carbs_g,
            fiber_g,
            sugar_g,
            sodium_mg,
            cholesterol_mg
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (meal_id,
        nutrition.get("calories"),
        nutrition.get("protein_g"),
        nutrition.get("fat_g"),
        nutrition.get("carbs_g"),
        nutrition.get("fiber_g"),
        nutrition.get("sugar_g"),
        nutrition.get("sodium_mg"),
        nutrition.get("cholesterol_mg"),),)

    conn.commit()
    conn.close()

    return meal_id
