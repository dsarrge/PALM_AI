from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from food_ai.vision.config import CLASS_INDEX_PATH, ONNX_MODEL_PATH
from food_ai.vision.backends.pytorch_backend import label_to_name
from food_ai.vision.train_classifier import validation_transforms
from food_ai.vision.types import DishPrediction


def _load_onnxruntime() -> Any:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise ImportError("onnxruntime is required for backend='onnx'. Install it with: pip install onnxruntime") from exc
    return ort


def _softmax(logits: np.ndarray) -> np.ndarray:
    logits = logits - np.max(logits, axis=1, keepdims=True)
    exp = np.exp(logits)
    return exp / np.sum(exp, axis=1, keepdims=True)


def _load_class_index(class_index_path: Path) -> tuple[dict[int, str], int]:
    if not class_index_path.exists():
        raise FileNotFoundError(
            f"Class index not found: {class_index_path}. "
            "Train the classifier first so class_index.json is saved."
        )
    payload = json.loads(class_index_path.read_text(encoding="utf-8"))
    idx_to_class = {int(index): label for index, label in payload["idx_to_class"].items()}
    img_size = int(payload.get("img_size", 224))
    return idx_to_class, img_size


def predict(
    image_path: str | Path,
    top_k: int = 5,
    model_path: Path = ONNX_MODEL_PATH,
    class_index_path: Path = CLASS_INDEX_PATH,
) -> list[DishPrediction]:
    model_path = Path(model_path)
    if not model_path.exists():
        raise FileNotFoundError(
            f"ONNX classifier model not found: {model_path}. "
            "Export one with: python -m food_ai.vision.export_onnx"
        )

    idx_to_class, img_size = _load_class_index(Path(class_index_path))
    transform = validation_transforms(img_size)

    with Image.open(image_path) as image:
        tensor = transform(image.convert("RGB")).unsqueeze(0)

    ort = _load_onnxruntime()
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if "CUDAExecutionProvider" in ort.get_available_providers() else ["CPUExecutionProvider"]
    session = ort.InferenceSession(str(model_path), providers=providers)
    logits = session.run(["logits"], {"input": tensor.numpy().astype(np.float32)})[0]
    probabilities = _softmax(logits)

    k = min(top_k, probabilities.shape[1])
    indices = np.argsort(probabilities[0])[-k:][::-1]
    predictions: list[DishPrediction] = []
    for index in indices:
        label = idx_to_class[int(index)]
        predictions.append(DishPrediction(label=label, name=label_to_name(label), confidence=float(probabilities[0, index])))
    return predictions
