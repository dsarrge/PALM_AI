from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DishPriorIngredient:
    dish_label: str
    ingredient: str
    weight: float
    source_count: int


@dataclass
class IngredientNutrition:
    ingredient: str
    calories: float | None
    protein_g: float | None
    total_fat_g: float | None
    total_carbs_g: float | None
    fiber_g: float | None
    total_sugars_g: float | None
    sodium_mg: float | None
    cholesterol_mg: float | None
    source: str | None
    fdc_id: str | int | None


@dataclass
class BridgeResult:
    status: str
    dish_predictions: list[dict[str, Any]]
    ingredients_ranked: list[dict[str, Any]]
    visible_ingredients: list[dict[str, Any]]
    ingredient_normalization: dict[str, Any]
    palm_ready: dict[str, Any]
    warnings: list[str]
