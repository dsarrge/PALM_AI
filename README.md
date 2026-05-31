# PALM Food AI

Offline-first food and nutrition tooling built around SQLite, barcode ingestion, and USDA-backed generic ingredient matching.

## Current Scope

- `food_ai/barcode/`: local barcode lookup plus OpenFoodFacts fetch/store helpers.
- `food_ai/nutrition.py`: canonical nutrition normalization and macro/micro alias handling.
- `food_ai/generic_foods/`: builds `generic_foods` and `generic_food_nutrition` rows from normalized ingredient nutrition.
- `food_ai/sync/`: RecipeNLG ingredient extraction and USDA nutrient loading workflows.
- `food_ai/usda/`: embedding cache, lexical matching, and portion helpers used by the sync pipeline.
- `food_ai/analytics/`: reporting helpers over the SQLite database.
- `food_ai/vision/`: offline dish reference indexing and image-to-dish inference.
- `shared/schema.sql`: checked-in base schema for `shared/palm.db`.

Most of the old `food_ai/core/*` modules are now compatibility wrappers around the package-level services above.

## Project Layout

```text
PALM/
|-- food_ai/
|   |-- __init__.py
|   |-- config.py
|   |-- db.py
|   |-- nutrition.py
|   |-- analytics/
|   |-- barcode/
|   |-- generic_foods/
|   |-- sync/
|   |-- usda/
|   |-- utils/
|   |-- vision/
|   `-- data/
|-- shared/
|   |-- palm.db
|   `-- schema.sql
|-- artifacts/
`-- scripts/
```
## Database

Default paths are defined in `food_ai/config.py`:

- legacy database: `shared/palm.db`
- PALM_V2 database: `shared/PALM_V2.db`
- schema: `shared/schema.sql`
- static data: `food_ai/data/`
- generated artifacts: `artifacts/`

Runtime nutrition lookups accept an explicit `db_path`. When no path is passed, the bridge prefers `shared/PALM_V2.db` if it exists and otherwise falls back to `shared/palm.db`.

Initialize the base schema with Python:

```python
from pathlib import Path
import sqlite3

schema = Path("shared/schema.sql").read_text(encoding="utf-8")
with sqlite3.connect("shared/palm.db") as connection:
    connection.executescript(schema)
```

Note: `food_ai.sync.extract_core_ingredients` creates the `core_ingredients` table on demand; it is not part of `shared/schema.sql`.

## Main APIs

Top-level imports exposed by `food_ai/__init__.py`:

```python
from food_ai import (
    build_generic_foods,
    infer_dish,
    lookup_barcode,
    normalize_nutrition,
    seed_generic_foods,
)
```
Example barcode lookup:

```python
from food_ai import lookup_barcode

product = lookup_barcode("012345678905", allow_online=False)
print(product["product_name"], product["calories"])
```

Example nutrition normalization:

```python
from food_ai import normalize_nutrition

normalized = normalize_nutrition(
    {
        "calories": 250,
        "protein_g": 10,
        "fat_g": 5,
        "carbs_g": 30,
    }
)
print(normalized["macros"])
```

Example dish inference:

```python
from food_ai import infer_dish

result = infer_dish("food_ai/tests/food_1.jpg", index_path="artifacts/dish_reference_index.npz")
print(result["dish"], result["confidence"])
```

Build a reference index from labeled image folders:

```bash
python -m food_ai.tools.infer_dish build path/to/dish_gallery
python -m food_ai.tools.infer_dish infer path/to/query.jpg --index artifacts/dish_reference_index.npz
```

## USDA / Generic Food Build Flow

Use this order for a fresh rebuild:

1. Initialize `shared/palm.db` from `shared/schema.sql`.
2. Extract high-frequency normalized ingredients:
   `python -m food_ai.sync.extract_core_ingredients`
3. Build the USDA embedding cache:
   `python -m food_ai.tools.build_usda_embeddings`
4. Match normalized ingredients to USDA foods and write `generic_ingredient_nutrition`:
   `python -m food_ai.sync.usda_ingredient_nutrition_loader`
5. Materialize `generic_foods` and `generic_food_nutrition`:
   `python -c "from food_ai import build_generic_foods; print(build_generic_foods(rebuild=True))"`

If you only need placeholder generic food rows, you can seed them without rebuilding nutrition:

```python
from food_ai import seed_generic_foods

print(seed_generic_foods())
```

## PALM_V2 USDA Build Flow

The PALM_V2 pipeline mirrors the required USDA/FDC CSVs into raw `usda_*` tables and then builds compact runtime nutrition tables for canonical foods, dish priors, portions, and branded barcode fallback.

Build `shared/PALM_V2.db`:

```bash
python -m food_ai.data.builders.build_palm_v2_db
```

Run validation explicitly:

```bash
python -m food_ai.validation.validate_palm_v2_db --db-path shared/PALM_V2.db
```

Required USDA inputs are read from `food_ai/data/USDA/`. The v2 runtime bridge uses only the compact PALM tables at inference time; the mirrored `usda_*` tables are rebuild inputs and validation evidence.

## Tests

Current targeted regression suite:

```bash
conda run -n palm-gpu311 python -m unittest food_ai.tests.test_refactor_core
conda run -n palm-gpu311 python -m unittest food_ai.tests.test_meal_log_service
```

## Notes

- Runtime dependencies visible in the code include `pandas`, `requests`, `tqdm`, `sentence-transformers`, and `torch`.
- Large USDA and recipe assets live in `food_ai/data/`; the embedding cache is stored at `food_ai/data/usda_embeddings.pkl`.
- `scripts/run_barcode.py` and `scripts/run_food_ai.py` predate the refactor and should be treated as legacy until they are brought in line with the current package layout.



## Streamlit Tester

A local desktop testing UI for the food vision pipeline is available at `food_ai/ui/app.py`.

Launch it with:

```bash
streamlit run food_ai/ui/app.py
```

Recommended local smoke test:

1. Launch the app.
2. Use the bundled smoke-test image from `food_ai/tests/food_1.jpg`.
3. Run inference.
4. Review fused dishes, ingredients, nutrition bridge output, warnings, fallback behavior, and saved local test logs.

This Streamlit UI is test-only infrastructure for local validation. It is intended to map later to:

- PC Streamlit tester -> current local service layer
- Jetson appliance -> future backend API layer
- mobile client -> future frontend consuming the same backend contract

## Production Meal Logger

A cleaner Streamlit meal logging UI is available at `food_ai/ui/production_app.py`. It reuses the maintained upload, inference, ingredient, nutrition, and runtime services, but writes reviewed meal logs to SQLite instead of local JSON test artifacts.

Launch it with:

```bash
streamlit run food_ai/ui/production_app.py
```

On the Windows PALM workstation, use:

```bash
launchers/run_palm_meal_logger.bat
```

The production app creates a `meal_logs` table on demand in the selected PALM database. By default that database is `shared/PALM_V2.db` when present, otherwise `shared/palm.db`. The saved row includes the image path, corrected dish name, confidence, serving grams, scaled nutrition totals, included/excluded ingredient JSON, notes, and the raw inference payload for traceability.
