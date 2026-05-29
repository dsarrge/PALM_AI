from __future__ import annotations

import argparse
import ast
import csv
import json
from pathlib import Path
from typing import Any

from food_ai.config import resolve_recipe_data_path


def _safe_list_count(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        parsed = ast.literal_eval(str(value))
    except (ValueError, SyntaxError):
        return len([part for part in str(value).split(",") if part.strip()])
    if isinstance(parsed, (list, tuple)):
        return len(parsed)
    return 1


def build_recipe_manifest(recipe_csv_path: str | Path | None, output_path: str | Path) -> Path:
    source_path = resolve_recipe_data_path(recipe_csv_path)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    write_jsonl = destination.suffix.lower() == ".jsonl"
    with source_path.open("r", encoding="utf-8", newline="") as source_handle, destination.open("w", encoding="utf-8", newline="") as output_handle:
        reader = csv.DictReader(source_handle)
        if write_jsonl:
            for index, row in enumerate(reader):
                payload = {
                    "recipe_id": row.get("Unnamed: 0", index),
                    "title": row.get("title", ""),
                    "link": row.get("link", ""),
                    "source": row.get("source", ""),
                    "ingredient_count": _safe_list_count(row.get("ingredients")),
                    "ner_count": _safe_list_count(row.get("NER")),
                    "direction_count": _safe_list_count(row.get("directions")),
                }
                output_handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
        else:
            output_handle.write("[\n")
            first = True
            for index, row in enumerate(reader):
                payload = {
                    "recipe_id": row.get("Unnamed: 0", index),
                    "title": row.get("title", ""),
                    "link": row.get("link", ""),
                    "source": row.get("source", ""),
                    "ingredient_count": _safe_list_count(row.get("ingredients")),
                    "ner_count": _safe_list_count(row.get("NER")),
                    "direction_count": _safe_list_count(row.get("directions")),
                }
                if not first:
                    output_handle.write(",\n")
                output_handle.write(json.dumps(payload, ensure_ascii=True))
                first = False
            output_handle.write("\n]\n")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a compact RecipeNLG manifest from the raw CSV dataset.")
    parser.add_argument("--recipe-csv", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = build_recipe_manifest(args.recipe_csv, args.output)
    print(output)


if __name__ == "__main__":
    main()
