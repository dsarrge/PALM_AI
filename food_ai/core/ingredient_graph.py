from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def load_recipe_rows(recipe_csv_path: str | Path) -> list[dict[str, Any]]:
    path = Path(recipe_csv_path)
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


def build_ingredient_graph(recipe_csv_path: str | Path) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for row in load_recipe_rows(recipe_csv_path):
        raw = str(row.get('ingredients', ''))
        cleaned = raw.strip('[]').replace('"', '').replace("'", '')
        graph[str(row.get('title', ''))] = {item.strip() for item in cleaned.split(',') if item.strip()}
    return graph
