from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from food_ai.food_bridge.build_dish_priors import _load_class_labels
from food_ai.food_bridge.config import DB_PATH, VISION_CLASS_INDEX_PATH


DISH_PRIOR_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dishes (
    dish_id INTEGER PRIMARY KEY AUTOINCREMENT,
    food101_class_idx INTEGER UNIQUE,
    food101_label TEXT UNIQUE NOT NULL,
    display_name TEXT,
    source TEXT DEFAULT 'food101',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dish_ingredient_priors (
    prior_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dish_id INTEGER NOT NULL,
    canonical_ingredient TEXT NOT NULL,
    ingredient_norm_key TEXT,
    fdc_id INTEGER,
    usda_description TEXT,
    prep_state TEXT,
    grams_mean REAL,
    grams_mode REAL,
    grams_median REAL,
    grams_std REAL,
    grams_default REAL,
    grams_per_100g_dish REAL,
    portion_method TEXT,
    pct_recipes_present REAL,
    ingredient_rank INTEGER,
    n_recipes INTEGER,
    n_weight_observations INTEGER,
    default_include INTEGER DEFAULT 1,
    confidence REAL,
    source TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dish_id) REFERENCES dishes(dish_id),
    UNIQUE (dish_id, canonical_ingredient, fdc_id)
);

CREATE TABLE IF NOT EXISTS dish_portion_priors (
    dish_id INTEGER PRIMARY KEY,
    dish_grams_mean REAL,
    serving_grams_median REAL,
    serving_grams_mode REAL,
    n_recipes INTEGER,
    confidence REAL,
    source TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dish_id) REFERENCES dishes(dish_id)
);

CREATE TABLE IF NOT EXISTS recipe_ingredient_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dish_id INTEGER NOT NULL,
    recipe_id TEXT,
    recipe_source TEXT,
    raw_ingredient_text TEXT,
    canonical_ingredient TEXT,
    ingredient_norm_key TEXT,
    parsed_quantity REAL,
    parsed_unit TEXT,
    parsed_grams REAL,
    servings REAL,
    grams_per_serving REAL,
    fdc_id INTEGER,
    usda_description TEXT,
    parse_confidence REAL,
    match_confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dish_id) REFERENCES dishes(dish_id)
);

CREATE TABLE IF NOT EXISTS inferred_dish_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dish_id INTEGER,
    dish_label TEXT NOT NULL,
    grams_consumed REAL,
    serving_estimate_grams REAL,
    selected_ingredients_json TEXT,
    excluded_ingredients_json TEXT,
    nutrition_snapshot_json TEXT NOT NULL,
    ingredient_snapshot_json TEXT NOT NULL,
    model_confidence REAL,
    bridge_confidence REAL,
    consumed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dish_id) REFERENCES dishes(dish_id)
);

CREATE INDEX IF NOT EXISTS idx_dish_ingredient_priors_dish_rank
    ON dish_ingredient_priors(dish_id, ingredient_rank);
CREATE INDEX IF NOT EXISTS idx_dish_ingredient_priors_fdc
    ON dish_ingredient_priors(fdc_id);
CREATE INDEX IF NOT EXISTS idx_recipe_observations_dish
    ON recipe_ingredient_observations(dish_id);
CREATE INDEX IF NOT EXISTS idx_inferred_dish_logs_consumed_at
    ON inferred_dish_logs(consumed_at);
"""


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_database(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    backup_path = db_path.with_name(f"{db_path.stem}_{_timestamp()}_before_dish_priors{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _class_labels_with_indexes(class_index_path: Path = VISION_CLASS_INDEX_PATH) -> list[tuple[int, str]]:
    if class_index_path.exists():
        payload = json.loads(class_index_path.read_text(encoding="utf-8"))
        class_to_idx = payload.get("class_to_idx")
        if isinstance(class_to_idx, dict) and class_to_idx:
            return sorted((int(index), str(label)) for label, index in class_to_idx.items())
    return [(index, label) for index, label in enumerate(_load_class_labels())]


def seed_dishes(connection: sqlite3.Connection) -> dict[str, int]:
    labels = _class_labels_with_indexes()
    inserted = 0
    updated = 0
    for class_idx, label in labels:
        display_name = label.replace("_", " ").title()
        existing = connection.execute(
            "SELECT dish_id FROM dishes WHERE food101_label = ? OR food101_class_idx = ?",
            (label, class_idx),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO dishes (food101_class_idx, food101_label, display_name, source, updated_at)
            VALUES (?, ?, ?, 'food101', CURRENT_TIMESTAMP)
            ON CONFLICT(food101_label) DO UPDATE SET
                food101_class_idx = excluded.food101_class_idx,
                display_name = excluded.display_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (class_idx, label, display_name),
        )
        if existing is None:
            inserted += 1
        else:
            updated += 1
    return {"inserted": inserted, "updated": updated, "total_labels": len(labels)}


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_migration_columns(connection: sqlite3.Connection) -> list[str]:
    added: list[str] = []
    prior_columns = _table_columns(connection, "dish_ingredient_priors")
    if "grams_per_100g_dish" not in prior_columns:
        connection.execute("ALTER TABLE dish_ingredient_priors ADD COLUMN grams_per_100g_dish REAL")
        added.append("dish_ingredient_priors.grams_per_100g_dish")
    portion_columns = _table_columns(connection, "dish_portion_priors")
    if "dish_grams_mean" not in portion_columns and "serving_grams_mean" in portion_columns:
        try:
            connection.execute("ALTER TABLE dish_portion_priors RENAME COLUMN serving_grams_mean TO dish_grams_mean")
            added.append("dish_portion_priors.dish_grams_mean")
        except sqlite3.OperationalError:
            connection.execute("ALTER TABLE dish_portion_priors ADD COLUMN dish_grams_mean REAL")
            connection.execute(
                "UPDATE dish_portion_priors SET dish_grams_mean = serving_grams_mean WHERE dish_grams_mean IS NULL"
            )
            added.append("dish_portion_priors.dish_grams_mean")
    elif "dish_grams_mean" not in portion_columns:
        connection.execute("ALTER TABLE dish_portion_priors ADD COLUMN dish_grams_mean REAL")
        added.append("dish_portion_priors.dish_grams_mean")
    elif "serving_grams_mean" in portion_columns:
        connection.execute(
            "UPDATE dish_portion_priors SET dish_grams_mean = serving_grams_mean WHERE dish_grams_mean IS NULL"
        )
    return added


def inspect_obsolete_tables(connection: sqlite3.Connection) -> list[str]:
    current_tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    known_live_tables = {
        "products",
        "barcodes",
        "serving_sizes",
        "nutrition_facts",
        "nutrition_micros",
        "consumption_logs",
        "unknown_barcodes",
        "generic_foods",
        "generic_food_nutrition",
        "food_units",
        "generic_ingredient_nutrition",
        "ingredient_aliases",
        "usda_portion_lookup_units",
        "usda_portion_lookup_rows",
        "dishes",
        "dish_ingredient_priors",
        "dish_portion_priors",
        "recipe_ingredient_observations",
        "inferred_dish_logs",
        "core_ingredients",
        "sqlite_sequence",
    }
    return sorted(table for table in current_tables if table not in known_live_tables and not table.startswith("archive_"))


def run_migration(db_path: str | Path = DB_PATH, create_backup: bool = True) -> dict[str, Any]:
    database_path = Path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = backup_database(database_path) if create_backup else None

    with closing(sqlite3.connect(database_path)) as connection:
        connection.executescript(DISH_PRIOR_SCHEMA_SQL)
        added_columns = ensure_migration_columns(connection)
        seed_summary = seed_dishes(connection)
        obsolete_candidates = inspect_obsolete_tables(connection)
        connection.commit()

        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("dishes", "dish_ingredient_priors", "dish_portion_priors", "recipe_ingredient_observations")
        }

    return {
        "db_path": str(database_path),
        "backup_path": str(backup_path) if backup_path else None,
        "columns_added": added_columns,
        "dishes": seed_summary,
        "table_counts": counts,
        "obsolete_table_candidates": obsolete_candidates,
        "tables_archived_or_dropped": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the DB-backed PALM dish ingredient prior tables.")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--no-backup", action="store_true", help="Skip the timestamped DB backup.")
    args = parser.parse_args()

    summary = run_migration(args.db_path, create_backup=not args.no_backup)
    print("PALM Dish Ingredient Prior Migration")
    print("=" * 38)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
