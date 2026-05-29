from food_ai.food_bridge.types import BridgeResult, DishPriorIngredient, IngredientNutrition


def build_food_bridge(*args, **kwargs):
    from food_ai.food_bridge.nutrition_bridge import build_food_bridge as _build_food_bridge

    return _build_food_bridge(*args, **kwargs)


__all__ = ["BridgeResult", "DishPriorIngredient", "IngredientNutrition", "build_food_bridge"]
