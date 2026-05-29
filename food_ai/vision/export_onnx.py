from __future__ import annotations

import argparse
from pathlib import Path

import torch

from food_ai.vision.config import MODEL_PATH, ONNX_MODEL_PATH
from food_ai.vision.backends.pytorch_backend import load_model


def export_onnx(model_path: Path = MODEL_PATH, onnx_model_path: Path = ONNX_MODEL_PATH) -> Path:
    model, idx_to_class, img_size, architecture = load_model(Path(model_path), device=torch.device("cpu"))
    dummy_input = torch.randn(1, 3, img_size, img_size, dtype=torch.float32)

    onnx_model_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        dummy_input,
        onnx_model_path,
        export_params=True,
        opset_version=17,
        do_constant_folding=True,
        input_names=["input"],
        output_names=["logits"],
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
    )

    if not onnx_model_path.exists():
        raise RuntimeError(f"ONNX export did not create expected file: {onnx_model_path}")

    print("PALM Food AI ONNX Export")
    print("=" * 24)
    print(f"Architecture: {architecture}")
    print(f"Classes: {len(idx_to_class)}")
    print(f"Image size: {img_size}")
    print(f"Checkpoint: {model_path}")
    print(f"ONNX model: {onnx_model_path}")
    return onnx_model_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the PALM dish classifier checkpoint to ONNX.")
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--output", type=Path, default=ONNX_MODEL_PATH)
    args = parser.parse_args()
    export_onnx(args.model_path, args.output)


if __name__ == "__main__":
    main()
