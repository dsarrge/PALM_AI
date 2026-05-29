from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_ROOT = PROJECT_ROOT / "artifacts" / "ui"
DEFAULT_UPLOADS_DIR = ARTIFACTS_ROOT / "uploads"
DEFAULT_LOGS_DIR = ARTIFACTS_ROOT / "test_logs"
DEFAULT_RUN_REPORTS_DIR = ARTIFACTS_ROOT / "run_reports"
DEFAULT_SAMPLE_IMAGES_ROOT = PROJECT_ROOT / "food_ai" / "tests"
DEFAULT_SAMPLE_IMAGE = PROJECT_ROOT / "food_ai" / "tests" / "food_1.jpg"
SUPPORTED_IMAGE_SUFFIXES = ("jpg", "jpeg", "png", "webp")
REVIEW_STATUS_OPTIONS = ("not_reviewed", "correct", "partially_correct", "incorrect")
EXPECTED_ENV_NAME = "palm-gpu311"


@dataclass(frozen=True, slots=True)
class UISettings:
    app_title: str = "PALM Food Vision Tester"
    app_description: str = (
        "Local desktop testing UI for PALM food vision. This is a tester for inference, "
        "nutrition bridging, and failure diagnostics. It is read-only by default and is not the final appliance or mobile UI."
    )
    uploads_dir: Path = DEFAULT_UPLOADS_DIR
    logs_dir: Path = DEFAULT_LOGS_DIR
    run_reports_dir: Path = DEFAULT_RUN_REPORTS_DIR
    sample_images_root: Path = DEFAULT_SAMPLE_IMAGES_ROOT
    sample_image_path: Path = DEFAULT_SAMPLE_IMAGE
    supported_image_suffixes: tuple[str, ...] = SUPPORTED_IMAGE_SUFFIXES
    expected_env_name: str = EXPECTED_ENV_NAME
