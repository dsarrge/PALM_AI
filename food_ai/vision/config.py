from __future__ import annotations

from pathlib import Path

from food_ai.config import ARTIFACTS_DIR, DATA_DIR, PROJECT_ROOT


FOOD101_IMAGE_DIR = DATA_DIR / "food-101" / "images"

VISION_ARTIFACTS_DIR = ARTIFACTS_DIR / "vision"
CLASSIFIER_DIR = VISION_ARTIFACTS_DIR / "classifier"
MODEL_PATH = CLASSIFIER_DIR / "food101_best.pt"
CLASS_INDEX_PATH = CLASSIFIER_DIR / "class_index.json"
ONNX_MODEL_PATH = CLASSIFIER_DIR / "food101_mobilenet_v3_large.onnx"

DEFAULT_ARCHITECTURE = "mobilenet_v3_large"
SUPPORTED_ARCHITECTURES = ["mobilenet_v3_large", "efficientnet_b0"]

IMG_SIZE = 224
BATCH_SIZE = 32
EPOCHS = 10
LR = 1e-4
VAL_SPLIT = 0.15
NUM_WORKERS = 4
SEED = 42

__all__ = [
    "BATCH_SIZE",
    "CLASSIFIER_DIR",
    "CLASS_INDEX_PATH",
    "DATA_DIR",
    "DEFAULT_ARCHITECTURE",
    "EPOCHS",
    "FOOD101_IMAGE_DIR",
    "IMG_SIZE",
    "LR",
    "MODEL_PATH",
    "NUM_WORKERS",
    "ONNX_MODEL_PATH",
    "PROJECT_ROOT",
    "SEED",
    "SUPPORTED_ARCHITECTURES",
    "VAL_SPLIT",
    "VISION_ARTIFACTS_DIR",
]
