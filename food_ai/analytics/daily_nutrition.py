from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd

from food_ai.config import DB_PATH


DAILY_NUTRITION_SQL = """
SELECT
    DATE(consumed_at) AS day,
    COUNT(*) AS entries,
    SUM(calories) AS calories,
    SUM(protein_g) AS protein_g,
    SUM(total_fat_g) AS total_fat_g,
    SUM(total_carbs_g) AS total_carbs_g,
    SUM(fiber_g) AS fiber_g,
    SUM(total_sugars_g) AS total_sugars_g,
    SUM(sodium_mg) AS sodium_mg,
    SUM(cholesterol_mg) AS cholesterol_mg
FROM consumption_logs
GROUP BY DATE(consumed_at)
ORDER BY day DESC
"""


def get_daily_nutrition(db_path: Path = DB_PATH) -> pd.DataFrame:
    connection = sqlite3.connect(db_path)
    try:
        return pd.read_sql_query(DAILY_NUTRITION_SQL, connection)
    finally:
        connection.close()


if __name__ == "__main__":
    print(get_daily_nutrition())
