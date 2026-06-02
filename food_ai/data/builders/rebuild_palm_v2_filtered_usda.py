"""Rebuild PALM V2 after removing excluded USDA FoodData Central data types.

This is intentionally a thin pre-filter around the existing PALM V2 builder.
It does not change runtime schema or ingredient logic; it materializes a cleaned
USDA CSV directory, then runs the normal downstream rebuild so nutrition,
portion priors, dish priors, aliases, and validation are recalculated from the
allowed USDA rows only.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from food_ai.config import PALM_V2_DB_PATH, USDA_DATA_DIR
from food_ai.data.builders.build_palm_v2_db import RAW_SOURCE_FILES, build_palm_v2_db
from food_ai.data.usda_seed_filter_policy import (
    EXCLUDED_USDA_DATA_TYPES,
    FDC_FILTER_RULES,
    FORCED_EMPTY_USDA_SOURCE_FILES,
    is_excluded_usda_data_type,
    normalize_fdc_id,
)
from food_ai.validation.validate_palm_v2_db import validate_palm_v2_db
from food_ai.validation.validate_palm_v2_usda_filter import validate_palm_v2_usda_filter


class FilterSummary(dict[str, Any]):
    pass


def _read_header(source_path: Path) -> list[str]:
    with source_path.open("r", encoding="utf-8", newline="", errors="replace") as handle:
        reader = csv.reader(handle)
        try:
            return next(reader)
        except StopIteration as exc:
            raise ValueError(f"CSV header missing for source file: {source_path}") from exc


def _write_rows(target_path: Path, header: list[str], rows: Iterable[dict[str, str | None]]) -> int:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with target_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            row_count += 1
    return row_count


def _copy_header_only(source_path: Path, target_path: Path) -> FilterSummary:
    header = _read_header(source_path)
    _write_rows(target_path, header, ())
    return FilterSummary(source_file=source_path.name, kept_rows=0, removed_rows=None, reason="forced_empty")


def _copy_food_csv(source_path: Path, target_path: Path) -> tuple[set[str], FilterSummary]:
    allowed_fdc_ids: set[str] = set()
    removed_rows = 0
    kept_rows = 0
    with source_path.open("r", encoding="utf-8", newline="", errors="replace") as source_handle:
        reader = csv.DictReader(source_handle)
        header = list(reader.fieldnames or [])
        if "fdc_id" not in header or "data_type" not in header:
            raise ValueError("food.csv must contain fdc_id and data_type columns")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("w", encoding="utf-8", newline="") as target_handle:
            writer = csv.DictWriter(target_handle, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                fdc_id = normalize_fdc_id(row.get("fdc_id"))
                if not fdc_id:
                    removed_rows += 1
                    continue
                if is_excluded_usda_data_type(row.get("data_type")):
                    removed_rows += 1
                    continue
                allowed_fdc_ids.add(fdc_id)
                writer.writerow(row)
                kept_rows += 1
    return allowed_fdc_ids, FilterSummary(source_file="food.csv", kept_rows=kept_rows, removed_rows=removed_rows, reason="data_type_filter")


def _row_is_allowed(row: dict[str, str | None], rules: tuple[tuple[str, bool], ...], allowed_fdc_ids: set[str]) -> bool:
    for column, required in rules:
        fdc_id = normalize_fdc_id(row.get(column))
        if not fdc_id:
            if required:
                return False
            continue
        if fdc_id not in allowed_fdc_ids:
            return False
    return True


def _copy_filtered_fdc_csv(source_path: Path, target_path: Path, allowed_fdc_ids: set[str]) -> FilterSummary:
    rules = FDC_FILTER_RULES[source_path.name]
    removed_rows = 0
    kept_rows = 0
    with source_path.open("r", encoding="utf-8", newline="", errors="replace") as source_handle:
        reader = csv.DictReader(source_handle)
        header = list(reader.fieldnames or [])
        missing_columns = [column for column, _ in rules if column not in header]
        if missing_columns:
            raise ValueError(f"{source_path.name} missing required filter columns: {missing_columns}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("w", encoding="utf-8", newline="") as target_handle:
            writer = csv.DictWriter(target_handle, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                if _row_is_allowed(row, rules, allowed_fdc_ids):
                    writer.writerow(row)
                    kept_rows += 1
                else:
                    removed_rows += 1
    return FilterSummary(source_file=source_path.name, kept_rows=kept_rows, removed_rows=removed_rows, reason="fdc_id_filter")


def materialize_filtered_usda_dir(source_dir: Path, target_dir: Path) -> dict[str, Any]:
    source_dir = Path(source_dir)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    source_files = tuple(filename for filename, _ in RAW_SOURCE_FILES)

    food_source = source_dir / "food.csv"
    if not food_source.exists():
        raise FileNotFoundError(f"Missing required USDA source file: {food_source}")
    allowed_fdc_ids, food_summary = _copy_food_csv(food_source, target_dir / "food.csv")

    summaries: dict[str, FilterSummary] = {"food.csv": food_summary}
    for filename in source_files:
        if filename == "food.csv":
            continue
        source_path = source_dir / filename
        target_path = target_dir / filename
        if not source_path.exists():
            raise FileNotFoundError(f"Missing required USDA source file: {source_path}")
        if filename in FORCED_EMPTY_USDA_SOURCE_FILES:
            summaries[filename] = _copy_header_only(source_path, target_path)
        elif filename in FDC_FILTER_RULES:
            summaries[filename] = _copy_filtered_fdc_csv(source_path, target_path, allowed_fdc_ids)
        else:
            shutil.copy2(source_path, target_path)
            summaries[filename] = FilterSummary(source_file=filename, kept_rows=None, removed_rows=0, reason="copied_reference")

    return {
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "excluded_data_types": EXCLUDED_USDA_DATA_TYPES,
        "allowed_food_rows": len(allowed_fdc_ids),
        "files": summaries,
    }


def rebuild_palm_v2_filtered_usda(
    output_db_path: str | Path = PALM_V2_DB_PATH,
    usda_data_dir: str | Path = USDA_DATA_DIR,
    filtered_usda_dir: str | Path | None = None,
    keep_filtered_usda_dir: bool = False,
    skip_validation: bool = False,
) -> dict[str, Any]:
    output_path = Path(output_db_path)
    source_dir = Path(usda_data_dir)

    if filtered_usda_dir is None:
        with tempfile.TemporaryDirectory(prefix="palm_v2_usda_filtered_") as temp_dir:
            filter_summary = materialize_filtered_usda_dir(source_dir, Path(temp_dir))
            build_summary = build_palm_v2_db(output_path, temp_dir)
            validation_summary = None if skip_validation else _run_validations(output_path, source_dir, filter_summary)
            return {"filter_summary": filter_summary, "build_summary": build_summary, "validation_summary": validation_summary}

    target_dir = Path(filtered_usda_dir)
    if target_dir.exists() and not keep_filtered_usda_dir:
        shutil.rmtree(target_dir)
    filter_summary = materialize_filtered_usda_dir(source_dir, target_dir)
    build_summary = build_palm_v2_db(output_path, target_dir)
    validation_summary = None if skip_validation else _run_validations(output_path, source_dir, filter_summary)
    return {"filter_summary": filter_summary, "build_summary": build_summary, "validation_summary": validation_summary}


def _run_validations(output_path: Path, original_usda_dir: Path, filter_summary: dict[str, Any]) -> dict[str, Any]:
    base_validation = validate_palm_v2_db(output_path, Path(filter_summary["target_dir"]))
    filter_validation = validate_palm_v2_usda_filter(output_path, original_usda_dir)
    return {
        "palm_v2_validation": base_validation,
        "usda_filter_validation": filter_validation,
        "passed": bool(base_validation.get("passed")) and bool(filter_validation.get("passed")),
    }


def _print_summary(summary: dict[str, Any]) -> None:
    filter_summary = summary["filter_summary"]
    build_summary = summary["build_summary"]
    print("PALM V2 Filtered USDA Rebuild")
    print("=" * 30)
    print("Excluded USDA data_types: " + ", ".join(filter_summary["excluded_data_types"]))
    print(f"Allowed food rows: {filter_summary['allowed_food_rows']}")
    print("Filtered source files:")
    for filename, payload in filter_summary["files"].items():
        kept = payload.get("kept_rows")
        removed = payload.get("removed_rows")
        kept_text = "copied" if kept is None else str(kept)
        removed_text = "n/a" if removed is None else str(removed)
        print(f"  {filename}: kept={kept_text} removed={removed_text} reason={payload.get('reason')}")
    print(f"DB path: {build_summary['db_path']}")
    print(f"Canonical foods built: {build_summary['canonical_foods_built']}")
    print(f"Dish ingredient priors built: {build_summary['dish_ingredient_priors_built']}")
    print(f"Products built: {build_summary['product_rows_built']}")
    print(f"Barcodes built: {build_summary['barcode_rows_built']}")
    validation = summary.get("validation_summary")
    if validation is not None:
        print("Validation: " + ("PASS" if validation.get("passed") else "FAIL"))
        for name in ("palm_v2_validation", "usda_filter_validation"):
            report = validation.get(name) or {}
            for error in report.get("errors") or []:
                print(f"  {name}: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild PALM V2 with excluded USDA data types removed.")
    parser.add_argument("--output-db", type=Path, default=PALM_V2_DB_PATH)
    parser.add_argument("--usda-data-dir", type=Path, default=USDA_DATA_DIR)
    parser.add_argument("--filtered-usda-dir", type=Path, default=None)
    parser.add_argument("--keep-filtered-usda-dir", action="store_true")
    parser.add_argument("--skip-validation", action="store_true")
    args = parser.parse_args()

    summary = rebuild_palm_v2_filtered_usda(
        output_db_path=args.output_db,
        usda_data_dir=args.usda_data_dir,
        filtered_usda_dir=args.filtered_usda_dir,
        keep_filtered_usda_dir=args.keep_filtered_usda_dir,
        skip_validation=args.skip_validation,
    )
    _print_summary(summary)
    validation = summary.get("validation_summary")
    if validation is not None and not validation.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
