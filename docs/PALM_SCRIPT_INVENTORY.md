# PALM Script Inventory

This inventory focuses on meaningful Python, launcher, build, validation, and UI scripts. Generated files, images, model binaries, raw datasets, caches, and database outputs are classified rather than exhaustively documented.

Status legend: ACTIVE, SUPPORTING, BUILD-TIME, VALIDATION, GENERATED, EXPERIMENTAL, LEGACY, OBSOLETE, UNKNOWN.

Recommendation legend: KEEP, CONSOLIDATE, ARCHIVE, REMOVE AFTER BACKUP, MANUAL REVIEW.

## Main Runtime And Build Scripts

| Script path | Purpose | Status | Evidence | Imported by / callers | Local imports | Key functions/classes | Files/DB touched | Removal risk | Recommendation |
|---|---|---|---|---|---|---|---|---|---|
| `food_ai/ui/app.py` | Streamlit tester entry point. | ACTIVE | README and launcher target it. | Streamlit runtime. | UI components/services, bridge. | `main`, `_run_inference`, `_recalculate_nutrition`. | Reads image uploads, calls PALM_V2 bridge. | HIGH | KEEP |
| `food_ai/ui/services/inference_service.py` | Runs vision inference and nutrition bridge. | ACTIVE | Imported by app and UI tests. | `food_ai/ui/app.py`. | `predict_dish`, `build_food_bridge`, `usda_portions`. | `run_food_inference`, `UIInferenceOptions`. | Reads images; reads PALM_V2 via bridge. | HIGH | KEEP |
| `food_ai/ui/services/formatters.py` | Converts bridge result to UI tables. | ACTIVE | Imported by app and tests; references evidence/debug fields. | UI app. | Runtime service. | `format_result_for_ui`, `_ingredient_rows`. | Formats bridge output; no DB direct. | HIGH | KEEP |
| `food_ai/ui/components/ingredients_panel.py` | Renders include/exclude ingredient table. | ACTIVE | Imported by UI component package. | UI app. | None. | `render_ingredients_panel`. | Streamlit state only. | HIGH | KEEP |
| `food_ai/vision/predict_dish.py` | Dish prediction API/CLI. | ACTIVE | Imported by UI and package. | UI inference service. | Vision config/types/backends. | `predict_dish`, `main`. | Reads model/image files. | HIGH | KEEP |
| `food_ai/vision/backends/pytorch_backend.py` | PyTorch model loading/prediction. | ACTIVE | Backend imported by prediction. | `predict_dish.py`. | Vision config/train/types. | `load_model`, `predict`. | Reads checkpoint/class index. | HIGH | KEEP |
| `food_ai/vision/backends/onnx_backend.py` | Optional ONNX prediction backend. | SUPPORTING | Backend imported by prediction. | `predict_dish.py`. | Vision config/train/types. | `predict`. | Reads ONNX/class index. | MEDIUM | KEEP |
| `food_ai/food_bridge/nutrition_bridge.py` | Runtime dish-to-ingredient-to-nutrition bridge. | ACTIVE | Imported by UI, tests, package. | UI, tests. | Config, bridge config, normalization, types, USDA matching. | `build_food_bridge`, `_load_v2_dish_priors`, `_blend_ingredients`, `_lookup_candidates`, `_calculate_*`. | Reads compact PALM_V2 tables. | HIGH | KEEP |
| `food_ai/food_bridge/types.py` | Bridge dataclasses. | ACTIVE | Imported by bridge/package. | Bridge. | None. | `DishPriorIngredient`, `IngredientNutrition`, `BridgeResult`. | None. | HIGH | KEEP |
| `food_ai/data/builders/build_palm_v2_db.py` | Canonical PALM_V2 DB builder. | BUILD-TIME | README documents it; validation imports it. | CLI, validation. | Config, RecipeNLG prior builder, ingredient normalization, USDA normalization, validation. | `build_palm_v2_db`, `_import_raw_usda_tables`, `_build_canonical_foods`, `_build_dish_priors`, `_classify_ingredient_candidate`. | Writes `shared/PALM_V2.db`; raw and compact tables. | HIGH | KEEP |
| `food_ai/validation/validate_palm_v2_db.py` | PALM_V2 validation/audit. | VALIDATION | README documents it; builder can run it. | CLI, builder. | Config, builder helpers. | `validate_palm_v2_db`, `_print_validation_report`. | Reads PALM_V2 tables. | HIGH | KEEP |
| `food_ai/config.py` | Central paths and runtime DB resolution. | ACTIVE | Imported broadly. | Many modules. | None. | `resolve_runtime_db_path`, `resolve_recipe_data_path`. | Defines DB/data/artifact paths. | HIGH | KEEP |
| `food_ai/db.py` | Legacy DB connect/init helper. | SUPPORTING | Imported by audit tools; simple wrapper. | `run_full_audit.py`. | Config. | `connect_db`, `initialize_db`. | Opens `shared/palm.db` unless passed. | MEDIUM | KEEP |

## Bridge, Priors, And Older Build Scripts

| Script path | Purpose | Status | Evidence | Imported by / callers | Local imports | Key functions/classes | Files/DB touched | Removal risk | Recommendation |
|---|---|---|---|---|---|---|---|---|---|
| `food_ai/food_bridge/build_dish_priors.py` | Builds RecipeNLG frequency priors. | BUILD-TIME | Imported by PALM_V2 builder and older DB prior builder. | Builder. | Bridge config, normalization. | `build_dish_priors`. | Reads RecipeNLG/class labels; writes JSON artifact. | HIGH | KEEP, but consolidate under V2 builder docs |
| `food_ai/food_bridge/build_dish_mass_priors.py` | Builds mass/gram priors from RecipeNLG measures. | BUILD-TIME | Imported by older DB prior builder and bridge fallbacks. | Older builder/bridge. | Dish priors, parser, portions. | `build_dish_mass_priors`. | Reads/writes JSON artifacts. | MEDIUM | CONSOLIDATE |
| `food_ai/food_bridge/build_db_dish_ingredient_priors.py` | Materializes older dish priors into DB. | LEGACY/BUILD-TIME | Writes `dish_ingredient_priors`; overlaps PALM_V2 builder. | Manual CLI. | Mass/frequency priors, migration, nutrition bridge. | `build_db_dish_ingredient_priors`. | Mutates DB prior table. | HIGH | ARCHIVE after confirming PALM_V2 replaces it |
| `food_ai/food_bridge/migrate_dish_ingredient_priors.py` | One-off migration/backup for old prior schema. | ONE-OFF/LEGACY | Migration naming; backup and schema alterations. | Imported by old DB prior builder. | Dish prior builder/config. | `run_migration`, `backup_database`, `ensure_migration_columns`. | Mutates/backs up DB. | HIGH | ARCHIVE after backup |
| `food_ai/food_bridge/build_nutrition5k_priors.py` | Nutrition5k frequency priors. | EXPERIMENTAL/SUPPORTING | Artifact builder not core PALM_V2. | Manual CLI. | Config, normalization, loader. | `build_nutrition5k_priors`. | Reads Nutrition5k; writes artifact. | LOW | MANUAL REVIEW |
| `food_ai/food_bridge/build_nutrition5k_mass_priors.py` | Nutrition5k mass priors. | EXPERIMENTAL/SUPPORTING | Similar to above. | Manual CLI. | Config, normalization, loader. | `build_nutrition5k_mass_priors`. | Reads Nutrition5k; writes artifact. | LOW | MANUAL REVIEW |
| `food_ai/food_bridge/nutrition5k_loader.py` | Inspect/detect Nutrition5k schema. | SUPPORTING/EXPERIMENTAL | Used by Nutrition5k prior builders. | Prior builders. | Config. | `inspect_nutrition5k`, `detect_nutrition5k_schema`. | Reads CSV metadata. | LOW | KEEP if Nutrition5k stays |
| `food_ai/food_bridge/smoke_test_dish_ingredient_priors.py` | Smoke test old bridge/barcode/log flow. | VALIDATION/LEGACY | Reads prior counts and bridge totals. | Manual CLI. | Barcode, daily totals, bridge. | `run_smoke`. | Reads DB, may call log/barcode flow. | MEDIUM | CONSOLIDATE into validation |
| `food_ai/food_bridge/usda_portions.py` | Old USDA portion lookup/import helper. | SUPPORTING/LEGACY | UI inference service imports audit helper; older names differ from PALM_V2. | UI inference, mass prior builder. | Bridge config. | `lookup_portion_grams`, `load_usda_portion_tables`. | Reads/writes old `usda_portions`-style tables. | MEDIUM | CONSOLIDATE carefully |
| `food_ai/food_bridge/ingredient_measure_parser.py` | Parses ingredient quantity/unit/name. | SUPPORTING | Imported by mass prior builders. | Mass prior builder. | Normalization, portions. | `parse_ingredient_measure`, `ParsedIngredientMeasure`. | None direct. | MEDIUM | KEEP |
| `food_ai/food_bridge/ingredient_normalize.py` | Current bridge ingredient normalization and aliases. | ACTIVE | Imported by bridge and PALM_V2 builder. | Builder, bridge, tests. | None. | `normalize_ingredient`, `ingredient_aliases_for`. | None. | HIGH | KEEP as canonical for bridge |

## Barcode, Generic Food, Core, And Analytics

| Script path | Purpose | Status | Evidence | Imported by / callers | Local imports | Key functions/classes | Files/DB touched | Removal risk | Recommendation |
|---|---|---|---|---|---|---|---|---|---|
| `food_ai/barcode/service.py` | Local barcode lookup/store service. | ACTIVE/SUPPORTING | Imported by core wrapper, tests, audit. | Tests, core, UI flows. | OpenFoodFacts/util paths. | `lookup_barcode`, store helpers. | `products`, `barcodes`, `serving_sizes`, `unknown_barcodes`. | HIGH | KEEP |
| `food_ai/barcode/openfoodfacts.py` | OpenFoodFacts client/parsing. | SUPPORTING | Tested by refactor suite. | Barcode service. | None. | product fetch/normalization helpers. | Network when enabled. | MEDIUM | KEEP |
| `food_ai/generic_foods/service.py` | Legacy generic food materialization. | LEGACY/SUPPORTING | README describes old `palm.db` flow. | Core wrappers/tests. | None/local. | generic food build functions. | Old generic food tables. | MEDIUM | MANUAL REVIEW |
| `food_ai/core/barcode_lookup.py` | Compatibility wrapper. | LEGACY/SUPPORTING | README says most core modules are wrappers. | External callers maybe. | Barcode service. | Wrapper only. | Barcode DB via service. | MEDIUM | KEEP until API users known |
| `food_ai/core/nutrition_engine.py` | Compatibility wrapper for nutrition. | LEGACY/SUPPORTING | Very small wrapper. | External callers maybe. | `food_ai.nutrition`. | None. | None. | LOW | CONSOLIDATE/API shim |
| `food_ai/core/log_consumption.py` | Meal logging helper. | SUPPORTING | Imported by smoke/tests. | Tests/smoke. | Config/nutrition. | logging functions. | `consumption_logs`. | MEDIUM | KEEP |
| `food_ai/core/daily_totals.py` | Daily nutrition aggregation. | SUPPORTING | Imported by smoke/tools. | Smoke/audit/today CLI. | Config. | daily totals functions. | `consumption_logs`. | MEDIUM | KEEP |
| `food_ai/analytics/daily_nutrition.py` | Daily nutrition query helper. | SUPPORTING | Reads `consumption_logs`. | Unknown. | Config. | `get_daily_nutrition`. | `consumption_logs`. | LOW | CONSOLIDATE with daily totals |
| `food_ai/nutrition.py` | Nutrition normalization aliases. | ACTIVE/SUPPORTING | Imported by tests/core. | Core/tests. | None. | `normalize_nutrition`. | None. | HIGH | KEEP |

## USDA, Sync, And Utility Scripts

| Script path | Purpose | Status | Evidence | Imported by / callers | Local imports | Key functions/classes | Files/DB touched | Removal risk | Recommendation |
|---|---|---|---|---|---|---|---|---|---|
| `food_ai/usda/matching.py` | Lexical USDA match scoring. | SUPPORTING | Imported by bridge, builder-related tests, USDA loader. | Bridge, sync, tests. | USDA normalization. | `lexical_match_score`. | None. | HIGH | KEEP |
| `food_ai/usda/service.py` | Legacy USDA ingredient match service. | LEGACY/SUPPORTING | Uses embeddings/catalog; old generic flow. | Audit/tests. | Config, sync loader, embeddings. | `match_ingredient_to_usda`. | Reads USDA CSV/cache. | MEDIUM | CONSOLIDATE later |
| `food_ai/usda/embeddings.py` | USDA embedding cache build/search. | SUPPORTING/LEGACY | Launcher rebuilds embeddings; audit uses it. | Tooling/service. | Config. | `build_or_load_usda_embeddings`, `cosine_similarity_search`. | `food_ai/data/usda_embeddings.pkl`. | MEDIUM | KEEP if old matching retained |
| `food_ai/usda/source.py` | Loads USDA food catalog CSV. | SUPPORTING/LEGACY | Used by USDA service. | USDA service. | USDA normalization. | `load_usda_food_catalog`. | Reads USDA CSV. | MEDIUM | KEEP until old service retired |
| `food_ai/usda/portions.py` | Tiny portion dataclass/helper. | SUPPORTING | Utility. | Unknown/tests maybe. | None. | `PortionEstimate`, `normalize_portion`. | None. | LOW | KEEP |
| `food_ai/sync/extract_core_ingredients.py` | Extracts RecipeNLG core ingredients for old flow. | LEGACY/BUILD-TIME | README old build flow. | Manual CLI/audit. | Config, utils normalization. | `extract_core_ingredients`, `rebuild_core_ingredients`. | Writes old `core_ingredients`. | MEDIUM | ARCHIVE after replacement |
| `food_ai/sync/usda_ingredient_nutrition_loader.py` | Selects representative USDA FDC for old generic ingredients. | SUPPORTING/LEGACY | Tested heavily; imported by USDA service. | Tests/service. | USDA matching. | `select_representative_fdc`. | Old generic nutrition load flow. | MEDIUM | KEEP until tests/behavior migrated |
| `food_ai/sync/usda_portion_loader.py` | Loads USDA portions into old table. | LEGACY/BUILD-TIME | References old `usda_portions`. | Manual CLI. | Config, bridge portions. | `load_usda_portions`. | Mutates old DB portion tables. | MEDIUM | ARCHIVE |
| `food_ai/sync/build_usda_backbone.py` | Builds old normalized USDA backbone artifact/table. | LEGACY/BUILD-TIME | Used by full audit. | Audit/manual. | Config, USDA normalization. | `build_usda_backbone`. | Writes artifacts/old tables. | LOW | ARCHIVE after audit replacement |
| `food_ai/utils/ingredient_normalize.py` | General ingredient line normalization. | SUPPORTING/LEGACY | Tested by refactor suite. | Sync/tests. | Ingredient parser. | `normalize_ingredient_line`, `normalize_ingredient_text`. | None. | MEDIUM | CONSOLIDATE with clear API boundary |
| `ingredient_normalize.py` | Root-level duplicate normalizer. | LEGACY | Mirrors utils-style normalization. | Unknown/manual. | `food_ai.utils.ingredient_parser`. | `normalize_ingredient_line`. | None. | LOW | ARCHIVE after import search |
| `food_ai/utils/usda_normalize.py` | USDA description normalization. | SUPPORTING | Imported by builder/matching/source/tests. | Builder, USDA modules. | None. | `normalize_usda_ingredient`. | None. | HIGH | KEEP |
| `food_ai/utils/usda_loader.py` | Tiny CSV loader. | SUPPORTING/LEGACY | Utility; no clear active caller in AST output. | Unknown. | None. | `load_usda_csv`. | Reads CSV. | LOW | MANUAL REVIEW |
| `food_ai/utils/openfood_store.py` | Wrapper for storing OpenFoodFacts products. | SUPPORTING | Tests import it. | Tests/manual. | Barcode service. | `store_openfood`. | `serving_sizes`, barcode/product tables. | MEDIUM | KEEP |

## Tools, Launchers, And Tests

| Script path | Purpose | Status | Evidence | Imported by / callers | Local imports | Key functions/classes | Files/DB touched | Removal risk | Recommendation |
|---|---|---|---|---|---|---|---|---|---|
| `launchers/run_palm_ui.bat` | Pinned Windows Streamlit launcher. | ACTIVE | Launcher README. | User. | N/A | N/A | Starts UI. | HIGH | KEEP |
| `launchers/run_food_smoke_test.bat` | Food vision smoke launcher. | VALIDATION | Launcher README. | User. | N/A | N/A | Writes smoke JSON. | MEDIUM | KEEP |
| `launchers/rebuild_usda_embeddings.bat` | Rebuild old USDA embeddings. | SUPPORTING/LEGACY | Launcher README. | User. | N/A | N/A | Writes embedding pickle. | LOW | MANUAL REVIEW |
| `launchers/palm_runtime_diagnostics.py` | Environment diagnostics. | SUPPORTING | Launcher README. | Runtime diagnostics launcher. | None. | `main`. | None. | LOW | KEEP |
| `food_ai/tools/run_full_audit.py` | Broad old audit harness. | VALIDATION/LEGACY | Imports many old modules and Streamlit smoke. | Manual CLI. | Many services. | `AuditRunner`, `run_audit`. | Copies/initializes DB, writes artifacts. | MEDIUM | CONSOLIDATE into focused audits |
| `food_ai/tools/build_usda_embeddings.py` | Embedding cache builder. | SUPPORTING/LEGACY | Launcher exists. | Manual CLI. | Config, USDA embeddings. | `build_usda_embeddings`. | Writes embeddings. | LOW | KEEP if old USDA service remains |
| `food_ai/tools/build_recipe_manifest.py` | Recipe manifest artifact builder. | SUPPORTING/LEGACY | Used by full audit. | Manual/audit. | Config. | `build_recipe_manifest`. | Writes manifest. | LOW | CONSOLIDATE |
| `food_ai/tools/image_to_log.py` | Thin image-to-log helper. | EXPERIMENTAL | Tiny wrapper around vision. | Manual. | Vision. | `image_to_log`. | Unknown. | LOW | MANUAL REVIEW |
| `food_ai/tools/today_cli.py` | Daily totals CLI. | SUPPORTING | Imports daily totals. | Manual. | Config, daily totals. | `main`. | Reads logs. | LOW | KEEP |
| `scripts/run_food_ai.py` | Old standalone food AI runner. | LEGACY | README says predates refactor; imports missing/old `food_ai.infer.meal_infer`. | User/manual. | Old infer module. | `run`. | Unknown. | LOW | ARCHIVE |
| `scripts/run_barcode.py` | Old barcode runner. | LEGACY | README marks legacy. | User/manual. | Unknown. | Unknown. | Barcode lookup. | LOW | ARCHIVE |
| `scripts/log_meal.py` | Standalone meal logging script. | LEGACY/ONE-OFF | Own DB init/log functions. | Manual. | None. | `init_db`, `log_meal`. | Mutates DB/logs. | MEDIUM | ARCHIVE after backup |
| `food_ai/tests/test_food_bridge.py` | Bridge regression suite. | VALIDATION | Active targeted test. | Unittest. | Bridge. | `FoodBridgeTests`. | Temp V2 tables. | HIGH | KEEP |
| `food_ai/tests/test_log_meal.py` | Barcode/log persistence tests. | VALIDATION | Active targeted test. | Unittest. | Barcode/log services. | `BarcodePersistenceCorrectnessTests`. | Temp DB. | HIGH | KEEP |
| `food_ai/tests/test_refactor_core.py` | Broad legacy/refactor tests. | VALIDATION | Large test file; many older APIs. | Unittest. | Many modules. | Multiple test classes. | Temp DB/artifacts. | MEDIUM | KEEP but split later |

## Important Function And Class Map

| File | Function/class | Role | Main inputs | Main outputs | Tables/files touched | Known callers | Logic type |
|---|---|---|---|---|---|---|---|
| `food_ai/config.py` | `resolve_runtime_db_path` | Chooses PALM_V2 when available unless path provided. | Optional path, `prefer_v2`. | `Path`. | `shared/PALM_V2.db`, `shared/palm.db`. | Bridge, UI, services. | helper/config |
| `food_ai/food_bridge/nutrition_bridge.py` | `build_food_bridge` | Main runtime nutrition bridge. | Dish predictions, optional DB path, serving grams/exclusions. | `BridgeResult`. | Reads compact PALM_V2 tables. | UI, tests. | nutrition/runtime |
| `food_ai/food_bridge/nutrition_bridge.py` | `_load_v2_dish_priors` | Loads PALM_V2 dish priors/evidence. | DB connection, dish candidates. | Prior ingredient rows. | `dish_*`, `canonical_*`. | `build_food_bridge`. | DB/nutrition |
| `food_ai/food_bridge/nutrition_bridge.py` | `_blend_ingredients` | Blends predicted dish priors into ranked ingredients. | Priors, prediction weights. | Ingredient list. | In-memory. | `build_food_bridge`. | nutrition |
| `food_ai/food_bridge/nutrition_bridge.py` | `_lookup_candidates_with_db_aliases` | Finds canonical nutrition candidates. | Ingredient text, DB. | Candidate nutrition rows. | `canonical_food_aliases`, `canonical_foods`, `canonical_food_nutrients`. | Bridge lookup flow. | DB/matching |
| `food_ai/data/builders/build_palm_v2_db.py` | `build_palm_v2_db` | End-to-end PALM_V2 build. | USDA dir, RecipeNLG artifacts, output DB. | SQLite DB. | Writes all PALM_V2 raw/compact tables. | CLI. | build-time DB |
| `food_ai/data/builders/build_palm_v2_db.py` | `_classify_ingredient_candidate` | Classifies candidates before prior insertion. | Candidate text/source/context. | `true_ingredient`, composite/evidence/reject classes. | In-memory/evidence table writer. | Dish prior build. | ingredient logic |
| `food_ai/data/builders/build_palm_v2_db.py` | `_extract_prep_state` | Detects cooked/raw/dry/unprepared states. | USDA description. | Prep-state label. | Used for canonical foods. | Canonical build. | nutrition correctness |
| `food_ai/data/builders/build_palm_v2_db.py` | `_build_products_and_barcodes` | Builds latest branded product/barcode fallback. | Raw branded/food/nutrient rows. | Product/barcode/serving rows. | `products`, `barcodes`, `serving_sizes`. | Builder. | build-time DB |
| `food_ai/validation/validate_palm_v2_db.py` | `validate_palm_v2_db` | Runs PALM_V2 checks. | DB path. | Result dict/status. | Reads all required V2 tables. | CLI/builder. | validation |
| `food_ai/validation/validate_palm_v2_db.py` | `_barcode_latest_publication_mismatches` | Validates latest branded UPC selection. | DB connection. | Mismatch rows. | `barcodes`, `products`, `usda_branded_food`, `usda_food`. | Validation. | validation |
| `food_ai/validation/validate_palm_v2_db.py` | `_pasta_prep_sanity_rows` | Finds dry/cooked pasta prep-state nutrition mismatches. | DB connection. | Offending rows. | `canonical_foods`, `canonical_food_nutrients`. | Validation. | validation |
| `food_ai/ui/services/inference_service.py` | `run_food_inference` | UI service wrapper for image inference + bridge. | Image path/options. | Pipeline payload. | Reads image; calls DB via bridge. | UI app/tests. | UI/runtime |
| `food_ai/ui/services/formatters.py` | `format_result_for_ui` | Converts raw result into display tables. | Pipeline payload. | UI-ready dict/tables. | None direct. | UI app. | UI |
| `food_ai/ui/components/ingredients_panel.py` | `render_ingredients_panel` | Renders ingredient include/exclude table. | Formatted rows/session state. | Streamlit controls. | None direct. | UI app. | UI |
| `food_ai/vision/predict_dish.py` | `predict_dish` | Image-to-dish inference. | Image path, backend, top-k. | Dish predictions. | Model/class index files. | UI/tests. | model |
| `food_ai/barcode/service.py` | `lookup_barcode` | Local/online product lookup. | Barcode, DB path, online flag. | Product dict. | `barcodes`, `products`, `serving_sizes`, `unknown_barcodes`. | Core/tests. | barcode/DB |
| `food_ai/nutrition.py` | `normalize_nutrition` | Normalizes nutrition aliases/macros. | Dict-like nutrition facts. | Normalized nutrition dict. | None. | Core/tests. | nutrition helper |

## Generated And Data Files

| Path/pattern | Classification | Notes |
|---|---|---|
| `artifacts/*.json`, `artifacts/**/*.json`, `artifacts/**/*.jsonl` | GENERATED | Prior manifests, audits, smoke outputs. Keep reproducible build docs before deletion. |
| `artifacts/**/*.pkl`, `food_ai/data/usda_embeddings.pkl` | CACHE/GENERATED | Embedding cache for older USDA matching. |
| `food_ai/data/USDA/*` | RAW DATA | USDA/FDC inputs. Do not edit by hand. |
| `food_ai/data/RecipeNLG/*` | RAW DATA | RecipeNLG source. Do not edit by hand. |
| `food_ai/data/food-101/*` | RAW DATA/MODEL DATA | Vision labels/images. |
| `shared/*.db` | DATABASE OUTPUTS | Do not delete without backup. `palm.db` is protected legacy working DB. |
| `__pycache__/` | CACHE | Safe generated cache. |
