from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DishPrediction:
    label: str
    name: str
    confidence: float


@dataclass
class PredictionResult:
    image_path: str
    predictions: list[DishPrediction]
    backend: str
    model_path: str
