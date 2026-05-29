from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from food_ai.vision import predict_dish


PROJECT_ROOT = Path(r"C:\PALM")
SMOKE_IMAGE = PROJECT_ROOT / "food_ai" / "tests" / "food_1.jpg"
OUTPUT_PATH = PROJECT_ROOT / "artifacts" / "vision" / "smoke" / "smoke_result.json"


def main() -> int:
    if not SMOKE_IMAGE.exists():
        print(f"ERROR: Smoke test image not found: {SMOKE_IMAGE}")
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result = predict_dish(image_path=SMOKE_IMAGE)
    OUTPUT_PATH.write_text(json.dumps(asdict(result), indent=2, default=str), encoding="utf-8")

    top_prediction = result.predictions[0] if result.predictions else None

    print("PALM Food Vision Smoke Test")
    print("=" * 30)
    print(f"Image: {SMOKE_IMAGE}")
    print(f"Backend: {result.backend}")
    print(f"Top dish: {top_prediction.name if top_prediction else 'none'}")
    print(f"Confidence: {top_prediction.confidence if top_prediction else 'n/a'}")
    print(f"Output JSON: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
