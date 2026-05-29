from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from food_ai.config import USDA_DATA_DIR
from food_ai.utils.usda_normalize import normalize_usda_ingredient


def build_usda_backbone(
    output_path: str | Path,
    food_csv_path: str | Path | None = None,
    include_branded: bool = False,
) -> dict[str, Any]:
    source_path = Path(food_csv_path) if food_csv_path is not None else USDA_DATA_DIR / "food.csv"
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    allowed_types = {
        "foundation_food",
        "sr_legacy_food",
        "survey_fndds_food",
        "sample_food",
        "agricultural_acquisition",
        "market_acquistion",
        "market_acquisition",
        "experimental_food",
    }
    if include_branded:
        allowed_types.add("branded_food")

    count = 0
    with source_path.open("r", encoding="utf-8", newline="") as source_handle, destination.open("w", encoding="utf-8", newline="") as output_handle:
        reader = csv.DictReader(source_handle)
        writer = csv.DictWriter(
            output_handle,
            fieldnames=["fdc_id", "data_type", "description", "food_category_id", "publication_date", "normalized_ingredient"],
        )
        writer.writeheader()
        for row in reader:
            data_type = str(row.get("data_type", "")).strip()
            if data_type not in allowed_types:
                continue
            writer.writerow(
                {
                    "fdc_id": row.get("fdc_id", ""),
                    "data_type": data_type,
                    "description": row.get("description", ""),
                    "food_category_id": row.get("food_category_id", ""),
                    "publication_date": row.get("publication_date", ""),
                    "normalized_ingredient": normalize_usda_ingredient(str(row.get("description", ""))) or "",
                }
            )
            count += 1
    return {"status": "ok", "output_path": str(destination), "row_count": count, "source_path": str(source_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a filtered USDA backbone CSV from the raw USDA food table.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--food-csv", type=Path, default=None)
    parser.add_argument("--include-branded", action="store_true")
    args = parser.parse_args()
    result = build_usda_backbone(args.output, food_csv_path=args.food_csv, include_branded=args.include_branded)
    print(result)


if __name__ == "__main__":
    main()
