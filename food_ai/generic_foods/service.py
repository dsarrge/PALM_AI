from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path


def default_prep_state(ingredient: str) -> str | None:
    lowered = ingredient.lower()
    if any(token in lowered for token in ("chicken", "beef", "pork", "snapper", "rice", "pasta")):
        return "cooked"
    return None


def seed_generic_foods(db_path: str | Path) -> int:
    del db_path
    return 0


def build_generic_foods(db_path: str | Path) -> int:
    with closing(sqlite3.connect(db_path)) as connection:
        foods = connection.execute("SELECT food_id, ingredient FROM generic_foods").fetchall()
        if not foods:
            source_rows = connection.execute("SELECT ingredient FROM generic_ingredient_nutrition").fetchall()
            for index, (ingredient,) in enumerate(source_rows, start=1):
                connection.execute(
                    "INSERT INTO generic_foods (food_id, food_name, ingredient, prep_state, source, created_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                    (index, str(ingredient).title(), ingredient, default_prep_state(str(ingredient)), "generated"),
                )
            foods = connection.execute("SELECT food_id, ingredient FROM generic_foods").fetchall()
        count = 0
        for food_id, ingredient in foods:
            row = connection.execute(
                "SELECT calories, protein_g, fat_g, carbs_g FROM generic_ingredient_nutrition WHERE ingredient = ?",
                (ingredient,),
            ).fetchone()
            if row is None:
                continue
            connection.execute(
                "INSERT OR REPLACE INTO generic_food_nutrition (food_id, calories, protein_g, total_fat_g, total_carbs_g) VALUES (?, ?, ?, ?, ?)",
                (food_id, float(row[0] or 0.0), float(row[1] or 0.0), float(row[2] or 0.0), float(row[3] or 0.0)),
            )
            count += 1
        connection.commit()
    return count
