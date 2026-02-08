# -*- coding: utf-8 -*-
"""
Created on Wed Dec 31 10:46:33 2025

@author: dsarr
"""

import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("C:/PALM/palm.db")

def get_daily_nutrition():
    sql = """
    SELECT
        DATE(timestamp_utc) AS day,
        COUNT(*)            AS meals,
        SUM(calories)       AS calories,
        SUM(protein_g)      AS protein_g,
        SUM(fat_g)          AS fat_g,
        SUM(carbs_g)        AS carbs_g,
        SUM(fiber_g)        AS fiber_g,
        SUM(sugar_g)        AS sugar_g,
        SUM(sodium_mg)      AS sodium_mg
    FROM meals
    JOIN meal_nutrition USING (meal_id)
    GROUP BY day
    ORDER BY day DESC;
    """

    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(sql, conn)

    return df

df = get_daily_nutrition()
print(df)
