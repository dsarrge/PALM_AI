from __future__ import annotations

from pathlib import Path

import pandas as pd

from food_ai.utils.usda_normalize import contains_phrase, normalize_usda_ingredient


REQUIRED_COLUMNS = ("fdc_id", "description")



def load_usda_food_catalog(csv_path: str | Path) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        return pd.DataFrame(columns=["fdc_id", "description", "normalized_ingredient"])
    frame = pd.read_csv(path)
    for column in REQUIRED_COLUMNS:
        if column not in frame.columns:
            raise ValueError(f"USDA source is missing required column: {column}")
    frame = frame.copy()
    frame["description"] = frame["description"].fillna("").astype(str)
    if "normalized_ingredient" not in frame.columns:
        frame["normalized_ingredient"] = frame["description"].map(lambda value: normalize_usda_ingredient(value) if value else None)
    if "category_name" not in frame.columns:
        frame["category_name"] = None
    return frame



def load_usda_source(csv_path: str | Path) -> pd.DataFrame:
    return load_usda_food_catalog(csv_path)


__all__ = ["contains_phrase", "load_usda_food_catalog", "load_usda_source", "normalize_usda_ingredient"]
