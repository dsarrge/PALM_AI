"""Shared USDA seed-filter policy for PALM V2 DB rebuilds."""

from __future__ import annotations

EXCLUDED_USDA_DATA_TYPES: tuple[str, ...] = (
    "branded_food",
    "experimental_food",
    "agricultural_acquisition",
    "market_acquisition",
    "sample_food",
    "sub_sample_food",
)

# The PALM V2 builder still expects this raw file name in its seed source set.
# For filtered rebuilds, it is materialized with headers only so USDA branded
# products/barcodes are not loaded into the runtime DB.
FORCED_EMPTY_USDA_SOURCE_FILES: frozenset[str] = frozenset({"branded_food.csv"})

# CSV files that carry FDC IDs and must be filtered after food.csv determines
# the allowed food universe. The boolean marks whether the field is required.
FDC_FILTER_RULES: dict[str, tuple[tuple[str, bool], ...]] = {
    "food_nutrient.csv": (("fdc_id", True),),
    "food_portion.csv": (("fdc_id", True),),
    "foundation_food.csv": (("fdc_id", True),),
    "sr_legacy_food.csv": (("fdc_id", True),),
    "survey_fndds_food.csv": (("fdc_id", True),),
    "input_food.csv": (("fdc_id", True), ("fdc_id_of_input_food", True)),
    "fndds_ingredient_nutrient_value.csv": (("FDC ID", False),),
}


def normalize_usda_data_type(value: object) -> str:
    return "_".join(str(value or "").strip().lower().split())


def normalize_fdc_id(value: object) -> str:
    return str(value or "").strip()


def is_excluded_usda_data_type(value: object) -> bool:
    return normalize_usda_data_type(value) in EXCLUDED_USDA_DATA_TYPES
