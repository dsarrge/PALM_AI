from __future__ import annotations

import argparse
from pathlib import Path

from food_ai.config import DATA_DIR, USDA_DATA_DIR
from food_ai.usda.embeddings import MODEL_NAME, build_or_load_usda_embeddings


DEFAULT_OUTPUT_PATH = DATA_DIR / "usda_embeddings.pkl"



def build_usda_embeddings(
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    food_csv_path: str | Path | None = None,
    model_name: str = MODEL_NAME,
    include_branded: bool = False,
    local_files_only: bool = True,
) -> Path:
    source_path = Path(food_csv_path) if food_csv_path is not None else USDA_DATA_DIR / "food.csv"
    destination = Path(output_path)
    build_or_load_usda_embeddings(
        source_path=source_path,
        output_path=destination,
        model_name=model_name,
        local_files_only=local_files_only,
        include_branded=include_branded,
        force_rebuild=True,
    )
    return destination



def main() -> None:
    parser = argparse.ArgumentParser(description="Build a USDA embedding cache from raw USDA food descriptions.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--food-csv", type=Path, default=None)
    parser.add_argument("--model-name", type=str, default=MODEL_NAME)
    parser.add_argument("--include-branded", action="store_true")
    parser.add_argument("--allow-download", action="store_true", help="Allow sentence-transformers to download model assets if they are not already cached locally.")
    args = parser.parse_args()
    output = build_usda_embeddings(
        output_path=args.output,
        food_csv_path=args.food_csv,
        model_name=args.model_name,
        include_branded=args.include_branded,
        local_files_only=not args.allow_download,
    )
    print(output)


if __name__ == "__main__":
    main()
