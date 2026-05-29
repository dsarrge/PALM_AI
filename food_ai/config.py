from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
DATA_DIR = PACKAGE_DIR / "data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
DB_PATH = PROJECT_ROOT / "shared" / "palm.db"
PALM_V2_DB_PATH = PROJECT_ROOT / "shared" / "PALM_V2.db"
SCHEMA_PATH = PROJECT_ROOT / "shared" / "schema.sql"
RECIPE_DATA_CANDIDATES = (
    DATA_DIR / "RecipeNLG" / "RecipeNLG_dataset.csv",
    DATA_DIR / "RecipeNLG_dataset.csv",
)
USDA_DATA_DIR = DATA_DIR / "USDA"
FOOD101_DATA_DIR = DATA_DIR / "food-101"


def resolve_recipe_data_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    for candidate in RECIPE_DATA_CANDIDATES:
        if candidate.exists():
            return candidate
    return RECIPE_DATA_CANDIDATES[0]


def resolve_runtime_db_path(path: str | Path | None = None, prefer_v2: bool = True) -> Path:
    if path is not None:
        return Path(path)
    if prefer_v2 and PALM_V2_DB_PATH.exists():
        return PALM_V2_DB_PATH
    return DB_PATH

__all__ = [
    "ARTIFACTS_DIR",
    "DATA_DIR",
    "DB_PATH",
    "FOOD101_DATA_DIR",
    "PALM_V2_DB_PATH",
    "PACKAGE_DIR",
    "PROJECT_ROOT",
    "RECIPE_DATA_CANDIDATES",
    "SCHEMA_PATH",
    "USDA_DATA_DIR",
    "resolve_runtime_db_path",
    "resolve_recipe_data_path",
]
