from __future__ import annotations

from pathlib import Path
from typing import Any

import torch
from PIL import Image

from food_ai.vision.config import MODEL_PATH
from food_ai.vision.train_classifier import build_model, validation_transforms
from food_ai.vision.types import DishPrediction


def label_to_name(label: str) -> str:
    return label.replace("_", " ")


def _load_checkpoint(model_path: Path) -> dict[str, Any]:
    if not model_path.exists():
        raise FileNotFoundError(
            f"PyTorch classifier checkpoint not found: {model_path}. "
            "Train one with: python -m food_ai.vision.train_classifier"
        )
    return torch.load(model_path, map_location="cpu")


def load_model(model_path: Path = MODEL_PATH, device: torch.device | None = None) -> tuple[torch.nn.Module, dict[int, str], int, str]:
    checkpoint = _load_checkpoint(Path(model_path))
    architecture = str(checkpoint["architecture"])
    num_classes = int(checkpoint["num_classes"])
    img_size = int(checkpoint.get("img_size", 224))
    idx_to_class = {int(index): label for index, label in checkpoint["idx_to_class"].items()}

    resolved_device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = build_model(architecture, num_classes, pretrained=False)
    model.load_state_dict(checkpoint["model_state_dict"])
    model.to(resolved_device)
    model.eval()
    return model, idx_to_class, img_size, architecture


def predict(image_path: str | Path, top_k: int = 5, model_path: Path = MODEL_PATH) -> list[DishPrediction]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, idx_to_class, img_size, _ = load_model(Path(model_path), device=device)
    transform = validation_transforms(img_size)

    with Image.open(image_path) as image:
        tensor = transform(image.convert("RGB")).unsqueeze(0).to(device)

    with torch.inference_mode():
        logits = model(tensor)
        probabilities = torch.softmax(logits, dim=1)
        values, indices = probabilities.topk(min(top_k, probabilities.shape[1]), dim=1)

    predictions: list[DishPrediction] = []
    for confidence, index in zip(values[0].cpu().tolist(), indices[0].cpu().tolist()):
        label = idx_to_class[int(index)]
        predictions.append(DishPrediction(label=label, name=label_to_name(label), confidence=float(confidence)))
    return predictions
