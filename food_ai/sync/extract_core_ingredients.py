from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import closing
import csv
import sqlite3
from pathlib import Path
from typing import Iterable

from food_ai.config import DB_PATH, resolve_recipe_data_path
from food_ai.utils.ingredient_normalize import normalize_ingredient_text


def _iter_ingredient_strings(row: dict[str, str]) -> Iterable[str]:
    for field in ("NER", "ingredients"):
        value = row.get(field)
        if value is None or value == "":
            continue
        try:
            parsed = ast.literal_eval(str(value))
        except (ValueError, SyntaxError):
            parsed = [part.strip() for part in str(value).split(",") if part.strip()]
        if isinstance(parsed, (list, tuple)):
            for item in parsed:
                yield str(item)


def extract_core_ingredients(recipe_csv_path: str | Path | None = None) -> list[str]:
    path = resolve_recipe_data_path(recipe_csv_path)
    items: list[str] = []
    if not path.exists():
        return items
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            for raw in _iter_ingredient_strings(row):
                normalized = normalize_ingredient_text(raw)
                if normalized:
                    items.append(normalized)
    return items


def rebuild_core_ingredients(
    recipe_csv_path: str | Path | None = None,
    db_path: str | Path = DB_PATH,
    min_recipe_count: int = 2,
) -> int:
    counts = Counter(extract_core_ingredients(recipe_csv_path))
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS core_ingredients (ingredient TEXT PRIMARY KEY, recipe_count INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute("DELETE FROM core_ingredients")
        for ingredient, recipe_count in counts.items():
            if recipe_count < min_recipe_count:
                continue
            connection.execute(
                "INSERT INTO core_ingredients (ingredient, recipe_count) VALUES (?, ?)",
                (ingredient, int(recipe_count)),
            )
        connection.commit()
        row_count = connection.execute("SELECT COUNT(*) FROM core_ingredients").fetchone()[0]
    return int(row_count)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract normalized core ingredients from RecipeNLG and optionally rebuild the SQLite table.")
    parser.add_argument("--recipe-csv", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--min-recipe-count", type=int, default=2)
    args = parser.parse_args()
    count = rebuild_core_ingredients(args.recipe_csv, db_path=args.db, min_recipe_count=args.min_recipe_count)
    print(count)


if __name__ == "__main__":
    main()
