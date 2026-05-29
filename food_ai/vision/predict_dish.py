from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from food_ai.vision.config import CLASS_INDEX_PATH, MODEL_PATH, ONNX_MODEL_PATH
from food_ai.vision.types import PredictionResult


def predict_dish(
    image_path: str | Path,
    top_k: int = 5,
    backend: str = "pytorch",
) -> PredictionResult:
    resolved_image_path = Path(image_path)
    if not resolved_image_path.exists():
        raise FileNotFoundError(f"Image not found: {resolved_image_path}")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")

    normalized_backend = backend.lower().strip()
    if normalized_backend == "pytorch":
        from food_ai.vision.backends.pytorch_backend import predict

        predictions = predict(resolved_image_path, top_k=top_k, model_path=MODEL_PATH)
        model_path = MODEL_PATH
    elif normalized_backend == "onnx":
        from food_ai.vision.backends.onnx_backend import predict

        predictions = predict(resolved_image_path, top_k=top_k, model_path=ONNX_MODEL_PATH, class_index_path=CLASS_INDEX_PATH)
        model_path = ONNX_MODEL_PATH
    else:
        raise ValueError("backend must be 'pytorch' or 'onnx'")

    return PredictionResult(
        image_path=str(resolved_image_path),
        predictions=predictions,
        backend=normalized_backend,
        model_path=str(model_path),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run dish classification on an image.")
    parser.add_argument("image_path", type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--backend", choices=["pytorch", "onnx"], default="pytorch")
    args = parser.parse_args()

    try:
        result = predict_dish(args.image_path, top_k=args.top_k, backend=args.backend)
    except Exception as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(json.dumps(asdict(result), indent=4))


if __name__ == "__main__":
    main()
