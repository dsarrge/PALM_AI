from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from food_ai.vision import predict_dish


def image_to_log(image_path: str | Path, output_path: str | Path, **kwargs: Any) -> Path:
    result = predict_dish(image_path=image_path, **kwargs)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(asdict(result), indent=2, default=str), encoding="utf-8")
    return destination
