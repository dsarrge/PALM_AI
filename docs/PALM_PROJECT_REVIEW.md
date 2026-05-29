# PALM Project Review

Audit date: 2026-05-29

Scope: static project review plus SQLite schema/count inspection for `shared/PALM_V2.db`. This document is a handoff for a new engineer. No code was refactored, deleted, moved, or rewritten during this audit.

## Executive Summary

PALM is a local food AI and nutrition bridge project. The current runtime path takes an image or dish label, predicts a Food-101-style dish, maps that dish to common user-facing ingredients, maps those ingredients to USDA-backed canonical foods, scales per-100g nutrients by ingredient grams, and renders the result in a Streamlit tester UI.

The most important current split is:

| Layer | Canonical current files | Notes |
|---|---|---|
| UI | `food_ai/ui/app.py`, `food_ai/ui/components/*`, `food_ai/ui/services/*` | Streamlit tester and formatting layer. |
| Vision | `food_ai/vision/predict_dish.py`, `food_ai/vision/backends/*` | Image-to-dish classification. |
| Nutrition bridge | `food_ai/food_bridge/nutrition_bridge.py` | Runtime dish-to-ingredient-to-nutrition flow. |
| PALM_V2 DB build | `food_ai/data/builders/build_palm_v2_db.py` | Imports USDA CSVs and materializes compact runtime tables. |
| PALM_V2 validation | `food_ai/validation/validate_palm_v2_db.py` | Validates raw imports, compact tables, ingredient logic, prep-state sanity, barcodes. |
| Legacy PALM DB flow | `food_ai/sync/*`, `food_ai/usda/*`, `food_ai/generic_foods/*` | Still useful in places, but overlaps with PALM_V2. |

The biggest engineering risk is not one broken file. It is overlapping generations of logic: old `palm.db` sync scripts, PALM_V2 builder logic, runtime bridge fallbacks, USDA matching helpers, and multiple normalization modules all coexist. That makes it easy for a future change to fix one path while silently leaving another path stale.

## Important Project Structure

Concise tree of relevant folders and files:

```text
C:\PALM
|-- README.md
|-- ingredient_normalize.py
|-- run_palm_ui.bat
|-- docs/
|-- launchers/
|   |-- run_palm_ui.bat
|   |-- run_food_smoke_test.bat
|   |-- rebuild_usda_embeddings.bat
|   |-- run_palm_runtime_diagnostics.bat
|   `-- palm_runtime_diagnostics.py
|-- scripts/
|   |-- run_food_ai.py
|   |-- run_barcode.py
|   `-- log_meal.py
|-- shared/
|   |-- palm.db
|   |-- PALM_V2.db
|   |-- schema.sql
|   `-- audit/
|-- artifacts/
|   |-- dish_priors*.json
|   |-- nutrition5k_*_priors.json
|   |-- recipe_manifest*.jsonl
|   |-- vision/
|   `-- usda/
`-- food_ai/
    |-- config.py
    |-- db.py
    |-- nutrition.py
    |-- analytics/
    |-- barcode/
    |-- core/
    |-- data/
    |   |-- builders/build_palm_v2_db.py
    |   |-- USDA/
    |   |-- RecipeNLG/
    |   `-- food-101/
    |-- food_bridge/
    |-- generic_foods/
    |-- sync/
    |-- tests/
    |-- tools/
    |-- ui/
    |-- usda/
    |-- utils/
    |-- validation/
    `-- vision/
```

Generated/cache/output areas should not be deeply hand-edited: `artifacts/`, `shared/*.db`, `shared/audit/`, `food_ai/data/USDA/`, `food_ai/data/RecipeNLG/`, `food_ai/data/food-101/`, and `__pycache__/`.

## Active Entry Points

| Path | Likely command | Purpose | Status | Evidence |
|---|---|---|---|---|
| `food_ai/ui/app.py` | `streamlit run food_ai/ui/app.py` | Local Streamlit tester for image upload, dish prediction, nutrition bridge, ingredient include/exclude, logging panel. | ACTIVE | README documents it; imports UI services/components and bridge; launchers target Streamlit app. |
| `launchers/run_palm_ui.bat` | double-click or run from shell | Windows launcher using pinned PALM Python environment. | ACTIVE | `launchers/README.md` describes it as primary UI launcher. |
| `food_ai/ui/services/inference_service.py` | imported by UI | Runs vision inference and nutrition bridge, handles smoke image. | ACTIVE | Imported by `food_ai/ui/app.py`; imports `predict_dish`, `build_food_bridge`, `usda_portions`. |
| `food_ai/vision/predict_dish.py` | `python -m food_ai.vision.predict_dish <image>` | Dish prediction CLI/API. | ACTIVE | Imported by UI inference service and `food_ai/vision/__init__.py`. |
| `food_ai/food_bridge/nutrition_bridge.py` | imported API: `build_food_bridge(...)` | Runtime ingredient and nutrition bridge. | ACTIVE | Imported by UI, tests, bridge package; reads compact PALM_V2 tables. |
| `food_ai/data/builders/build_palm_v2_db.py` | `python -m food_ai.data.builders.build_palm_v2_db --output-db shared/PALM_V2.db` | Builds PALM_V2 raw mirror tables and compact runtime tables. | BUILD-TIME | README documents it; validation imports helper functions; directly references required USDA and compact tables. |
| `food_ai/validation/validate_palm_v2_db.py` | `python -m food_ai.validation.validate_palm_v2_db --db-path shared/PALM_V2.db` | PALM_V2 validation/audit report. | VALIDATION | README documents it; builder can run validation. |
| `food_ai/tests/test_food_bridge.py` | `python -m unittest food_ai.tests.test_food_bridge` | Regression tests for bridge and PALM_V2 runtime-table use. | VALIDATION | Imports bridge and creates V2-shaped temp tables. |
| `food_ai/tests/test_log_meal.py` | `python -m unittest food_ai.tests.test_log_meal` | Barcode/product/serving/logging persistence tests. | VALIDATION | Exercises `barcodes`, `products`, `serving_sizes`, `consumption_logs`. |
| `food_ai/tests/test_refactor_core.py` | `python -m unittest food_ai.tests.test_refactor_core` | Broad legacy/refactor regression suite. | VALIDATION | Tests nutrition normalization, barcode, USDA matching, ingredient parsing, vision-related APIs. |

## End-To-End Architecture Walkthrough

1. Image or user input enters the Streamlit tester through `food_ai/ui/app.py`.
2. The UI calls `food_ai/ui/services/inference_service.py::run_food_inference`.
3. Inference calls `food_ai/vision/predict_dish.py::predict_dish`, which dispatches to the PyTorch or ONNX backend in `food_ai/vision/backends/`.
4. The top dish candidates are sent to `food_ai/food_bridge/nutrition_bridge.py::build_food_bridge`.
5. The bridge resolves the DB path with `food_ai/config.py::resolve_runtime_db_path`. By default it prefers `shared/PALM_V2.db` when present and falls back to `shared/palm.db`.
6. The bridge reads PALM_V2 compact tables:
   - `dish_labels` and `dish_aliases` for dish key matching.
   - `dish_ingredient_priors` for user-facing true ingredients and original grams per 100g dish.
   - `dish_variant_evidence` for debug/evidence parent dish rows.
   - `canonical_foods`, `canonical_food_aliases`, and `canonical_food_nutrients` for USDA-backed ingredient nutrition.
7. Ingredient nutrition is calculated with the USDA/FDC per-100g rule: `ingredient_grams * nutrient_per_100g / 100`.
8. The bridge returns ingredient-level contributions, adjusted grams after exclusions, evidence rows, warnings, and totals in a `BridgeResult`.
9. `food_ai/ui/services/formatters.py` formats the bridge result into UI tables.
10. `food_ai/ui/components/ingredients_panel.py` renders the include/exclude ingredient table. Composite dish evidence should not appear here.
11. If logging is used, barcode/product/meal data flows through `food_ai/core/log_consumption.py`, `food_ai/core/daily_totals.py`, and barcode services into `consumption_logs` and `meal_log_ingredients`.

## PALM_V2 Build Walkthrough

`food_ai/data/builders/build_palm_v2_db.py` is the canonical PALM_V2 builder. It:

- Imports required USDA CSVs exactly into raw mirror tables named `usda_*`.
- Writes `usda_import_manifest` with source file, raw table, row count, column count, schema hash, and import time.
- Builds `canonical_foods`, `canonical_food_aliases`, `canonical_food_nutrients`, `nutrition_sources`.
- Applies source priority: Foundation/SR/FNDDS first for generic ingredients, branded only for product/barcode fallback.
- Builds `food_portion_priors` from `usda_food_portion` plus `usda_measure_unit`.
- Builds `dish_labels`, `dish_aliases`, `dish_variant_evidence`, and `dish_ingredient_priors`.
- Uses RecipeNLG for common ingredient identity/prevalence, FNDDS `input_food.gram_weight` for composition evidence, and USDA canonical foods for nutrition.
- Builds `products`, `barcodes`, and `serving_sizes` from latest USDA branded records per `gtin_upc`.

## How To Run For A New Developer

Environment observed in launchers:

```powershell
C:\Users\dsarr\AppData\Local\spyder-6\envs\palm-gpu311\python.exe
```

Run the Streamlit tester:

```powershell
streamlit run food_ai/ui/app.py
```

Or use:

```powershell
launchers\run_palm_ui.bat
```

Build PALM_V2:

```powershell
python -m food_ai.data.builders.build_palm_v2_db --output-db shared/PALM_V2.db
```

Validate PALM_V2:

```powershell
python -m food_ai.validation.validate_palm_v2_db --db-path shared/PALM_V2.db
```

Run focused tests:

```powershell
python -m unittest food_ai.tests.test_food_bridge food_ai.tests.test_log_meal
```

Manually validate a dish:

- Launch the UI and run a sample image, or call `build_food_bridge` from Python with dish candidates.
- For lasagna, verify the ingredient table shows ingredients like noodles/pasta, sauce, beef/meat, ricotta/mozzarella/parmesan, onion/garlic/seasoning.
- Verify debug evidence may include FNDDS parent dish variants, but the user-facing ingredient table does not.

Common failure points:

- Missing `shared/PALM_V2.db`, causing fallback to `shared/palm.db`.
- Missing USDA CSVs in `food_ai/data/USDA/`.
- Pinned Windows interpreter path moved.
- Streamlit installed in a different environment than the PALM launcher uses.
- Raw USDA data is very large; accidental full-table scans are slow.
- Duplicate old scripts may write to `palm.db` or older table names instead of `PALM_V2.db`.

## Known Risks And Technical Debt

- Hardcoded Windows paths exist in launchers and some historical workflows.
- `palm.db` and `PALM_V2.db` coexist, and some legacy code still targets the old DB model.
- Multiple ingredient normalization modules exist; changing the wrong one may not affect PALM_V2.
- Multiple USDA matching/build flows exist; PALM_V2 should be treated as canonical for the new runtime path.
- Destructive or mutating DB scripts exist without a formal migration ledger.
- Some tests still exercise older refactor paths and may not fully protect PALM_V2 ingredient logic.
- Raw USDA tables are present in `PALM_V2.db` as rebuild/validation evidence; runtime should stay on compact tables.
- Dish aliases and composite-vs-ingredient classification are inherently ambiguous.
- Restaurant entrée or packaged food leakage into user-facing ingredients remains a critical validation target.
- Pasta/noodle prep-state handling must remain protected because dry and cooked forms have very different nutrients.

## Final Summary

### 10 Most Important Active Scripts

1. `food_ai/ui/app.py`
2. `food_ai/ui/services/inference_service.py`
3. `food_ai/ui/services/formatters.py`
4. `food_ai/ui/components/ingredients_panel.py`
5. `food_ai/vision/predict_dish.py`
6. `food_ai/food_bridge/nutrition_bridge.py`
7. `food_ai/data/builders/build_palm_v2_db.py`
8. `food_ai/validation/validate_palm_v2_db.py`
9. `food_ai/barcode/service.py`
10. `food_ai/config.py`

### Safest Cleanup Wins

- Archive generated audit outputs and old smoke JSON outputs after confirming they are not referenced.
- Add a clear `archive/` area for old one-off repair/migration scripts.
- Mark `scripts/run_food_ai.py` and old root-level wrappers as legacy unless actively used.
- Keep PALM_V2 build/validation docs current so new work does not revive older sync paths accidentally.

### Scripts That Should Remain Separate

- UI code and runtime nutrition bridge.
- Runtime bridge and PALM_V2 DB builder.
- PALM_V2 builder and destructive/repair migration scripts.
- Vision training/export code and runtime prediction code.
- Barcode product lookup and generic dish ingredient nutrition.

### Dangerous Mutating Scripts

- `food_ai/data/builders/build_palm_v2_db.py`
- `food_ai/food_bridge/migrate_dish_ingredient_priors.py`
- `food_ai/food_bridge/build_db_dish_ingredient_priors.py`
- `food_ai/sync/usda_portion_loader.py`
- `food_ai/sync/extract_core_ingredients.py`
- `food_ai/tools/run_full_audit.py`
- `scripts/log_meal.py`

### Next 5 Actions

1. Freeze `build_palm_v2_db.py`, `nutrition_bridge.py`, and `validate_palm_v2_db.py` as the canonical PALM_V2 path in README.
2. Add a migration/archive policy before deleting any script.
3. Consolidate duplicated ingredient normalization wrappers without changing behavior.
4. Consolidate old dish-prior build scripts behind PALM_V2 builder or archive them.
5. Add regression tests for representative dish ingredient tables and nutrition contributions.
