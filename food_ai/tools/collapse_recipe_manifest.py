from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def collapse_recipe_manifest(input_paths: list[str | Path], output_path: str | Path) -> Path:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in input_paths:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
        for row in payload:
            key = (row.get('title', ''), row.get('link', ''))
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(merged, indent=2), encoding='utf-8')
    return destination
