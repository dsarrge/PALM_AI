from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
from typing import Any

from food_ai.config import PALM_V2_DB_PATH

ML_UNITS = ("ML", "MILLILITER", "MILLILITERS", "MILLILITRE", "MILLILITRES")
GRAM_UNITS = ("G", "GRAM", "GRAMS")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ml_placeholders() -> str:
    return ", ".join("?" for _ in ML_UNITS)


def cleanup_branded_ml_serving_sizes(db_path: str | Path = PALM_V2_DB_PATH) -> dict[str, Any]:
    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(db)
    with sqlite3.connect(db) as conn:
        if not _table_exists(conn, "serving_sizes"):
            return {"db_path": str(db), "removed_rows": 0, "reason": "serving_sizes_missing"}
        columns = _columns(conn, "serving_sizes")
        if not {"measure_unit_name", "source"}.issubset(columns):
            return {"db_path": str(db), "removed_rows": 0, "reason": "required_columns_missing"}
        cursor = conn.execute(
            f"""
            DELETE FROM serving_sizes
            WHERE LOWER(COALESCE(source, '')) = 'usda_branded'
              AND UPPER(TRIM(COALESCE(measure_unit_name, ''))) IN ({_ml_placeholders()})
            """,
            ML_UNITS,
        )
        removed = int(cursor.rowcount or 0)
        conn.commit()
    return {
        "db_path": str(db),
        "removed_rows": removed,
        "reason": "branded_ml_serving_sizes_are_not_grams_without_density",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=PALM_V2_DB_PATH)
    args = parser.parse_args()
    print(cleanup_branded_ml_serving_sizes(args.db))


if __name__ == "__main__":
    main()
