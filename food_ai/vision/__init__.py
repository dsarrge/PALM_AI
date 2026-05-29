from food_ai.vision.types import DishPrediction, PredictionResult


def predict_dish(*args, **kwargs):
    from food_ai.vision.predict_dish import predict_dish as _predict_dish

    return _predict_dish(*args, **kwargs)


__all__ = ["predict_dish", "DishPrediction", "PredictionResult"]
