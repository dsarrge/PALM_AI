from __future__ import annotations

from pathlib import Path
from typing import Any

from food_ai.config import DB_PATH
from food_ai.food_bridge.usda_portions import load_usda_portion_tables


def load_usda_portions(csv_path: str | Path, db_path: str | Path | None = None) -> dict[str, Any]:
    return load_usda_portion_tables(
        db_path=db_path if db_path is not None else DB_PATH,
        food_portion_csv_path=csv_path,
    )
