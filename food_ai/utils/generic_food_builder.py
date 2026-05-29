from __future__ import annotations

from pathlib import Path

from food_ai.generic_foods.service import build_generic_foods


def build_generic_food_builder(db_path: str | Path) -> int:
    return build_generic_foods(db_path)
