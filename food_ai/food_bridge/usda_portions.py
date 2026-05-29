from __future__ import annotations

import argparse
import csv
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from food_ai.food_bridge.config import DB_PATH, USDA_FOOD_PORTION_PATH, USDA_MEASURE_UNIT_PATH


UNIT_ALIASES = {
    "c": "cup",
    "c.": "cup",
    "cup": "cup",
    "cups": "cup",
    "tablespoon": "tablespoon",
    "tablespoons": "tablespoon",
    "tbsp": "tablespoon",
    "tbs": "tablespoon",
    "teaspoon": "teaspoon",
    "teaspoons": "teaspoon",
    "tsp": "teaspoon",
    "ounce": "ounce",
    "ounces": "ounce",
    "oz": "ounce",
    "pound": "pound",
    "pounds": "pound",
    "lb": "pound",
    "lbs": "pound",
    "gram": "gram",
    "grams": "gram",
    "g": "gram",
    "kilogram": "kilogram",
    "kilograms": "kilogram",
    "kg": "kilogram",
    "milliliter": "milliliter",
    "milliliters": "milliliter",
    "ml": "milliliter",
    "liter": "liter",
    "liters": "liter",
    "l": "liter",
    "fluid ounce": "fluid_ounce",
    "fluid ounces": "fluid_ounce",
    "fl oz": "fluid_ounce",
    "fl. oz": "fluid_ounce",
    "slice": "slice",
    "slices": "slice",
    "piece": "piece",
    "pieces": "piece",
    "clove": "clove",
    "cloves": "clove",
    "can": "can",
    "cans": "can",
    "stick": "stick",
    "sticks": "stick",
}

COMMON_UNITS = ("cup", "tablespoon", "teaspoon", "ounce", "pound", "gram", "slice", "can", "stick")
USDA_PORTION_UNIT_TABLE = "usda_portion_lookup_units"
USDA_PORTION_ROW_TABLE = "usda_portion_lookup_rows"


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    text = str(unit).strip().lower()
    if not text:
        return None
    text = text.replace(".", "")
    text = " ".join(text.replace("-", " ").split())
    return UNIT_ALIASES.get(text, text.replace(" ", "_"))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def ensure_usda_portion_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {USDA_PORTION_UNIT_TABLE} (
            measure_unit_id INTEGER PRIMARY KEY,
            unit_name TEXT NOT NULL,
            normalized_unit TEXT NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {USDA_PORTION_ROW_TABLE} (
            fdc_id INTEGER NOT NULL,
            amount REAL,
            measure_unit_id INTEGER,
            unit_name TEXT,
            normalized_unit TEXT,
            portion_description TEXT,
            modifier TEXT,
            gram_weight REAL NOT NULL,
            data_points INTEGER,
            PRIMARY KEY (fdc_id, amount, measure_unit_id, portion_description, modifier, gram_weight)
        )
        """
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{USDA_PORTION_ROW_TABLE}_lookup ON {USDA_PORTION_ROW_TABLE}(fdc_id, normalized_unit)"
    )


def load_usda_portion_tables(
    db_path: str | Path = DB_PATH,
    food_portion_csv_path: str | Path = USDA_FOOD_PORTION_PATH,
    measure_unit_csv_path: str | Path = USDA_MEASURE_UNIT_PATH,
) -> dict[str, Any]:
    database_path = Path(db_path)
    food_portion_path = Path(food_portion_csv_path)
    measure_unit_path = Path(measure_unit_csv_path)
    if not measure_unit_path.exists():
        raise FileNotFoundError(f"USDA measure_unit.csv not found: {measure_unit_path}")
    if not food_portion_path.exists():
        raise FileNotFoundError(f"USDA food_portion.csv not found: {food_portion_path}")

    measure_units: dict[int, tuple[str, str]] = {}
    skipped_units = 0
    with measure_unit_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            measure_unit_id = _as_int(row.get("id"))
            unit_name = str(row.get("name") or "").strip()
            normalized_unit = normalize_unit(unit_name)
            if measure_unit_id is None or not unit_name or not normalized_unit:
                skipped_units += 1
                continue
            measure_units[measure_unit_id] = (unit_name, normalized_unit)

    loaded_portions = 0
    skipped_portions = 0
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as connection:
        ensure_usda_portion_schema(connection)
        connection.execute(f"DELETE FROM {USDA_PORTION_UNIT_TABLE}")
        connection.execute(f"DELETE FROM {USDA_PORTION_ROW_TABLE}")
        connection.executemany(
            f"INSERT INTO {USDA_PORTION_UNIT_TABLE} (measure_unit_id, unit_name, normalized_unit) VALUES (?, ?, ?)",
            [(measure_unit_id, unit_name, normalized_unit) for measure_unit_id, (unit_name, normalized_unit) in measure_units.items()],
        )

        with food_portion_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                fdc_id = _as_int(row.get("fdc_id"))
                gram_weight = _as_float(row.get("gram_weight"))
                if fdc_id is None or gram_weight is None or gram_weight <= 0:
                    skipped_portions += 1
                    continue
                amount = _as_float(row.get("amount"))
                measure_unit_id = _as_int(row.get("measure_unit_id"))
                unit_name, normalized_unit = measure_units.get(measure_unit_id or -1, ("", None))
                modifier = str(row.get("modifier") or "").strip()
                if not normalized_unit and modifier:
                    normalized_unit = normalize_unit(modifier)
                    unit_name = modifier
                connection.execute(
                    f"""
                    INSERT OR REPLACE INTO {USDA_PORTION_ROW_TABLE} (
                        fdc_id, amount, measure_unit_id, unit_name, normalized_unit,
                        portion_description, modifier, gram_weight, data_points
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fdc_id,
                        amount,
                        measure_unit_id,
                        unit_name,
                        normalized_unit,
                        str(row.get("portion_description") or "").strip(),
                        modifier,
                        gram_weight,
                        _as_int(row.get("data_points")),
                    ),
                )
                loaded_portions += 1
        connection.commit()

    return {
        "db_path": str(database_path),
        "measure_units_loaded": len(measure_units),
        "measure_units_skipped": skipped_units,
        "food_portion_rows_loaded": loaded_portions,
        "food_portion_rows_skipped": skipped_portions,
        "distinct_normalized_units": len({unit for _, unit in measure_units.values()}),
    }


def _row_score(row: sqlite3.Row) -> tuple[int, int, float]:
    modifier = str(row["modifier"] or "").strip().lower()
    description = str(row["portion_description"] or "").strip().lower()
    data_points = _as_float(row["data_points"]) or 0.0
    return (
        0 if modifier in ("", "serving") else 1,
        0 if description == "" else 1,
        -data_points,
    )


def lookup_portion_grams(
    db_path: str | Path,
    fdc_id: int,
    unit: str,
    amount: float = 1.0,
    portion_description: str | None = None,
) -> dict[str, Any]:
    normalized_unit = normalize_unit(unit)
    if not normalized_unit:
        return {
            "status": "not_found",
            "fdc_id": fdc_id,
            "unit": unit,
            "normalized_unit": None,
            "amount": amount,
            "grams": None,
            "gram_weight_per_unit": None,
            "match_method": "not_found",
            "source": USDA_PORTION_ROW_TABLE,
            "reason": "unsupported_unit",
        }

    database_path = Path(db_path)
    if not database_path.exists():
        return {
            "status": "not_found",
            "fdc_id": fdc_id,
            "unit": unit,
            "normalized_unit": normalized_unit,
            "amount": amount,
            "grams": None,
            "gram_weight_per_unit": None,
            "match_method": "not_found",
            "source": USDA_PORTION_ROW_TABLE,
            "reason": "db_path_missing",
        }

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        ensure_usda_portion_schema(connection)
        rows = connection.execute(
            f"""
            SELECT * FROM {USDA_PORTION_ROW_TABLE}
            WHERE fdc_id = ? AND normalized_unit = ? AND gram_weight IS NOT NULL
            """,
            (int(fdc_id), normalized_unit),
        ).fetchall()
        match_method = "exact_unit"
        if not rows and portion_description:
            pattern = f"%{portion_description.strip()}%"
            rows = connection.execute(
                f"""
                SELECT * FROM {USDA_PORTION_ROW_TABLE}
                WHERE fdc_id = ?
                  AND (LOWER(portion_description) LIKE LOWER(?) OR LOWER(modifier) LIKE LOWER(?))
                  AND gram_weight IS NOT NULL
                """,
                (int(fdc_id), pattern, pattern),
            ).fetchall()
            match_method = "description_like"

    if not rows:
        return {
            "status": "not_found",
            "fdc_id": fdc_id,
            "unit": unit,
            "normalized_unit": normalized_unit,
            "amount": amount,
            "grams": None,
            "gram_weight_per_unit": None,
            "match_method": "not_found",
            "source": USDA_PORTION_ROW_TABLE,
            "reason": "no_matching_food_portion",
        }

    best = sorted(rows, key=_row_score)[0]
    portion_amount = _as_float(best["amount"]) or 1.0
    gram_weight = float(best["gram_weight"])
    gram_weight_per_unit = gram_weight / portion_amount if portion_amount else gram_weight
    grams = gram_weight_per_unit * float(amount)
    return {
        "status": "found",
        "fdc_id": int(fdc_id),
        "unit": unit,
        "normalized_unit": normalized_unit,
        "amount": float(amount),
        "grams": round(grams, 6),
        "gram_weight_per_unit": round(gram_weight_per_unit, 6),
        "match_method": match_method,
        "source": USDA_PORTION_ROW_TABLE,
        "portion_description": best["portion_description"],
        "modifier": best["modifier"],
        "measure_unit_id": best["measure_unit_id"],
    }


def audit_usda_portion_tables(db_path: str | Path = DB_PATH) -> dict[str, Any]:
    database_path = Path(db_path)
    report: dict[str, Any] = {
        "db_path": str(database_path),
        "db_exists": database_path.exists(),
        "usda_measure_units_count": 0,
        "usda_food_portions_count": 0,
        "distinct_normalized_units": 0,
        "common_units_present": {},
    }
    if not database_path.exists():
        return report
    with closing(sqlite3.connect(database_path)) as connection:
        ensure_usda_portion_schema(connection)
        report["usda_measure_units_count"] = int(connection.execute(f"SELECT COUNT(*) FROM {USDA_PORTION_UNIT_TABLE}").fetchone()[0])
        report["usda_food_portions_count"] = int(connection.execute(f"SELECT COUNT(*) FROM {USDA_PORTION_ROW_TABLE}").fetchone()[0])
        report["distinct_normalized_units"] = int(connection.execute(f"SELECT COUNT(DISTINCT normalized_unit) FROM {USDA_PORTION_ROW_TABLE}").fetchone()[0])
        for unit in COMMON_UNITS:
            count = int(connection.execute(f"SELECT COUNT(*) FROM {USDA_PORTION_ROW_TABLE} WHERE normalized_unit = ?", (unit,)).fetchone()[0])
            report["common_units_present"][unit] = count > 0
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Load or audit USDA measure units and food portions into PALM DB.")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--food-portion", type=Path, default=USDA_FOOD_PORTION_PATH)
    parser.add_argument("--measure-unit", type=Path, default=USDA_MEASURE_UNIT_PATH)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--lookup-fdc-id", type=int)
    parser.add_argument("--unit")
    parser.add_argument("--amount", type=float, default=1.0)
    args = parser.parse_args()

    if args.audit:
        print(audit_usda_portion_tables(args.db_path))
        return
    if args.lookup_fdc_id is not None and args.unit:
        print(lookup_portion_grams(args.db_path, args.lookup_fdc_id, args.unit, args.amount))
        return

    result = load_usda_portion_tables(args.db_path, args.food_portion, args.measure_unit)
    print("PALM USDA Portion Loader")
    print("=" * 24)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
