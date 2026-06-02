"""Validate that filtered PALM V2 builds do not retain excluded USDA data types."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Any

from food_ai.config import PALM_V2_DB_PATH, USDA_DATA_DIR
from food_ai.data.usda_seed_filter_policy import EXCLUDED_USDA_DATA_TYPES, normalize_usda_data_type

EXCLUDED_RUNTIME_SOURCES: tuple[str, ...] = (
    "branded_food",
    "experimental_food",
    "agricultural_acquisition",
    "market_acquisition",
    "sample_food",
    "sub_sample_food",
    "usda_branded",
    "usda_branded_food",
)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _load_excluded_fdc_ids(usda_data_dir: Path) -> tuple[set[str], Counter[str]]:
    food_csv = usda_data_dir / "food.csv"
    if not food_csv.exists():
        raise FileNotFoundError(f"Missing USDA food.csv: {food_csv}")

    excluded_ids: set[str] = set()
    counts: Counter[str] = Counter()
    with food_csv.open("r", encoding="utf-8", newline="", errors="replace") as handle:
        reader = csv.DictReader(handle)
        if "fdc_id" not in (reader.fieldnames or []) or "data_type" not in (reader.fieldnames or []):
            raise ValueError("food.csv must contain fdc_id and data_type columns")
        for row in reader:
            data_type = normalize_usda_data_type(row.get("data_type"))
            if data_type in EXCLUDED_USDA_DATA_TYPES:
                fdc_id = str(row.get("fdc_id") or "").strip()
                if fdc_id:
                    excluded_ids.add(fdc_id)
                    counts[data_type] += 1
    return excluded_ids, counts


def _count_excluded_raw_food_rows(connection: sqlite3.Connection) -> int:
    if not _table_exists(connection, "usda_food"):
        return 0
    placeholders = ", ".join("?" for _ in EXCLUDED_USDA_DATA_TYPES)
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS row_count
        FROM usda_food
        WHERE LOWER(TRIM(COALESCE(data_type, ''))) IN ({placeholders})
        """,
        EXCLUDED_USDA_DATA_TYPES,
    ).fetchone()
    return int(row["row_count"] or 0)


def _find_id_leaks(connection: sqlite3.Connection, excluded_fdc_ids: set[str]) -> list[str]:
    if not excluded_fdc_ids:
        return []
    leak_checks = (
        ("usda_food", "fdc_id"),
        ("usda_food_nutrient", "fdc_id"),
        ("usda_food_portion", "fdc_id"),
        ("usda_foundation_food", "fdc_id"),
        ("usda_sr_legacy_food", "fdc_id"),
        ("usda_survey_fndds_food", "fdc_id"),
        ("usda_input_food", "fdc_id"),
        ("usda_input_food", "fdc_id_of_input_food"),
        ("usda_fndds_ingredient_nutrient_value", "FDC ID"),
        ("canonical_foods", "preferred_fdc_id"),
        ("canonical_food_nutrients", "source_fdc_id"),
        ("nutrition_sources", "source_fdc_id"),
        ("food_portion_priors", "source_fdc_id"),
        ("dish_variant_evidence", "source_fdc_id"),
        ("products", "source_fdc_id"),
        ("barcodes", "source_fdc_id"),
        ("meal_log_ingredients", "source_fdc_id"),
    )
    temp_values = [(fdc_id,) for fdc_id in excluded_fdc_ids]
    connection.execute("DROP TABLE IF EXISTS temp_excluded_fdc_ids")
    connection.execute("CREATE TEMP TABLE temp_excluded_fdc_ids (fdc_id TEXT PRIMARY KEY)")
    connection.executemany("INSERT INTO temp_excluded_fdc_ids (fdc_id) VALUES (?)", temp_values)

    leaks: list[str] = []
    for table, column in leak_checks:
        if not _table_exists(connection, table):
            continue
        row = connection.execute(
            f"""
            SELECT COUNT(*) AS leak_count
            FROM {table} t
            INNER JOIN temp_excluded_fdc_ids x ON x.fdc_id = CAST(t."{column}" AS TEXT)
            """
        ).fetchone()
        leak_count = int(row["leak_count"] or 0)
        if leak_count:
            leaks.append(f"{table}.{column} contains {leak_count} excluded USDA FDC IDs")
    connection.execute("DROP TABLE IF EXISTS temp_excluded_fdc_ids")
    return leaks


def _find_runtime_source_leaks(connection: sqlite3.Connection) -> list[str]:
    leaks: list[str] = []
    source_checks = (
        ("canonical_foods", "preferred_source"),
        ("nutrition_sources", "source_type"),
        ("products", "source"),
        ("barcodes", "source"),
        ("serving_sizes", "source"),
        ("dish_variant_evidence", "source"),
    )
    placeholders = ", ".join("?" for _ in EXCLUDED_RUNTIME_SOURCES)
    for table, column in source_checks:
        if not _table_exists(connection, table):
            continue
        row = connection.execute(
            f"""
            SELECT COUNT(*) AS leak_count
            FROM {table}
            WHERE LOWER(TRIM(COALESCE({column}, ''))) IN ({placeholders})
            """,
            EXCLUDED_RUNTIME_SOURCES,
        ).fetchone()
        leak_count = int(row["leak_count"] or 0)
        if leak_count:
            leaks.append(f"{table}.{column} contains {leak_count} excluded runtime source rows")
    return leaks


def validate_palm_v2_usda_filter(
    db_path: str | Path = PALM_V2_DB_PATH,
    usda_data_dir: str | Path = USDA_DATA_DIR,
) -> dict[str, Any]:
    database_path = Path(db_path)
    data_dir = Path(usda_data_dir)
    errors: list[str] = []
    warnings: list[str] = []

    if not database_path.exists():
        return {"passed": False, "errors": [f"Database not found: {database_path}"], "warnings": warnings}

    excluded_fdc_ids, excluded_counts = _load_excluded_fdc_ids(data_dir)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        raw_food_leaks = _count_excluded_raw_food_rows(connection)
        if raw_food_leaks:
            errors.append(f"usda_food contains {raw_food_leaks} excluded data_type rows")
        errors.extend(_find_id_leaks(connection, excluded_fdc_ids))
        errors.extend(_find_runtime_source_leaks(connection))

        if _table_exists(connection, "products"):
            product_count = int(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0])
            if product_count:
                errors.append(f"products table contains {product_count} rows after branded USDA exclusion")
        if _table_exists(connection, "barcodes"):
            barcode_count = int(connection.execute("SELECT COUNT(*) FROM barcodes").fetchone()[0])
            if barcode_count:
                errors.append(f"barcodes table contains {barcode_count} rows after branded USDA exclusion")
        if _table_exists(connection, "serving_sizes"):
            serving_count = int(connection.execute("SELECT COUNT(*) FROM serving_sizes").fetchone()[0])
            if serving_count:
                errors.append(f"serving_sizes table contains {serving_count} rows after branded USDA exclusion")

    return {
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "excluded_data_types": EXCLUDED_USDA_DATA_TYPES,
        "excluded_fdc_id_count": len(excluded_fdc_ids),
        "excluded_counts_by_data_type": dict(sorted(excluded_counts.items())),
    }


def _print_report(report: dict[str, Any]) -> None:
    print("PALM V2 USDA Filter Validation")
    print("=" * 31)
    print("PASS" if report["passed"] else "FAIL")
    print("Excluded data_types: " + ", ".join(report.get("excluded_data_types") or ()))
    print(f"Excluded FDC IDs from source food.csv: {report.get('excluded_fdc_id_count')}")
    for data_type, count in (report.get("excluded_counts_by_data_type") or {}).items():
        print(f"  {data_type}: {count}")
    if report.get("errors"):
        print("Errors:")
        for error in report["errors"]:
            print(f"  - {error}")
    if report.get("warnings"):
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate excluded USDA data types are absent from PALM V2.")
    parser.add_argument("--db-path", type=Path, default=PALM_V2_DB_PATH)
    parser.add_argument("--usda-data-dir", type=Path, default=USDA_DATA_DIR)
    args = parser.parse_args()

    report = validate_palm_v2_usda_filter(args.db_path, args.usda_data_dir)
    _print_report(report)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
