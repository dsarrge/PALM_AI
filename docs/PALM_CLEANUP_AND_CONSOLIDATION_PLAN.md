# PALM Cleanup And Consolidation Plan

This plan is recommendation-only. Do not delete, move, or rewrite files until there is a backup and a small validation suite that proves behavior is unchanged.

## Duplicate Logic Review

| Duplicate area | Files/functions involved | Canonical-looking implementation | Older/redundant candidates | Risk of keeping both | Recommendation |
|---|---|---|---|---|---|
| Ingredient normalization | `food_ai/food_bridge/ingredient_normalize.py`, `food_ai/utils/ingredient_normalize.py`, root `ingredient_normalize.py` | `food_ai/food_bridge/ingredient_normalize.py` for PALM_V2 bridge/build; `food_ai/utils/ingredient_normalize.py` for older RecipeNLG/core tests. | Root `ingredient_normalize.py` likely duplicate/manual wrapper. | HIGH: changing one normalizer may not affect runtime; RecipeNLG compatibility can break silently. | Freeze bridge normalizer as canonical for PALM_V2. Archive root duplicate after import search. Later define one public API with compatibility wrappers. |
| USDA/FDC matching | `food_ai/usda/matching.py`, `food_ai/usda/service.py`, `food_ai/sync/usda_ingredient_nutrition_loader.py`, `food_ai/data/builders/build_palm_v2_db.py`, bridge lookup helpers | PALM_V2 builder for precomputed runtime mapping; `food_ai/usda/matching.py` for shared lexical scoring. | Old embedding/service/sync path. | HIGH: generic ingredient truth may come from different source priority rules. | Keep `matching.py`; keep old service only until callers/tests are migrated. Ensure runtime does not fuzzy match common foods. |
| Dish prior building | `build_palm_v2_db.py`, `build_dish_priors.py`, `build_dish_mass_priors.py`, `build_db_dish_ingredient_priors.py`, Nutrition5k builders | `build_palm_v2_db.py` for final DB; `build_dish_priors.py` for RecipeNLG frequency support. | Old DB materializer and one-off migration. | HIGH: old scripts can rebuild stale/incorrect `dish_ingredient_priors`. | Consolidate old prior materialization into PALM_V2 builder or archive. |
| Portion logic | `food_ai/food_bridge/usda_portions.py`, `food_ai/usda/portions.py`, `food_ai/sync/usda_portion_loader.py`, `ingredient_measure_parser.py`, PALM_V2 `food_portion_priors` | PALM_V2 `food_portion_priors` for runtime; `ingredient_measure_parser.py` for parsing RecipeNLG units. | Old `usda_portions` loader/table helpers. | MEDIUM: old plural/legacy table names can reappear. | Move old loader to archive after confirming UI no longer needs audit call; keep parser separate. |
| Nutrition calculation | `food_ai/food_bridge/nutrition_bridge.py`, `food_ai/nutrition.py`, `food_ai/core/nutrition_engine.py`, barcode service scaling logic | `nutrition_bridge.py` for dish ingredients; `food_ai/nutrition.py` for nutrition-facts normalization. | `core/nutrition_engine.py` wrapper. | MEDIUM: barcode nutrition and dish nutrition can diverge naming/scaling. | Keep separate domains but document formulas and field aliases. |
| Nutrient mapping | PALM_V2 builder nutrient map, `food_ai/nutrition.py`, old USDA loader | PALM_V2 builder validated against `usda_nutrient`. | Old loader mappings. | HIGH: wrong nutrient IDs corrupt all nutrition. | Add tests around core nutrient IDs and PALM column mapping; avoid duplicated maps. |
| DB connection helpers | `food_ai/config.py`, `food_ai/db.py`, direct `sqlite3.connect` in scripts | `food_ai/config.py::resolve_runtime_db_path`. | Direct hardcoded DB paths. | MEDIUM: scripts can mutate `palm.db` when PALM_V2 expected. | Require mutating scripts to accept explicit `--db-path`; document old DB flow as legacy. |
| Validation/debug/export | `validate_palm_v2_db.py`, `smoke_test_dish_ingredient_priors.py`, `run_full_audit.py`, UI diagnostic harness | `validate_palm_v2_db.py` for PALM_V2 correctness. | Broad full audit and old smoke tests. | LOW/MEDIUM: duplicate reports are noisy and may disagree. | Merge PALM_V2 checks into validation; keep full audit only for environment smoke. |
| Streamlit nutrition rendering | `ui/services/formatters.py`, `ui/components/ingredients_panel.py`, `debug_panel.py`, older recipes panel | Formatter plus ingredients/nutrition panels. | `recipes_panel.py` appears placeholder/old. | LOW: mostly UI clutter. | Keep UI components split; remove unused placeholder only after UI import check. |

## Script Consolidation Candidates

| Group name | Scripts involved | Why they belong together | Recommended final module/script | Functions to keep | Functions to archive/remove | Risk | Benefit | Validation test |
|---|---|---|---|---|---|---|---|---|
| PALM_V2 dish-prior build path | `build_palm_v2_db.py`, `build_dish_priors.py`, `build_dish_mass_priors.py`, `build_db_dish_ingredient_priors.py` | They all generate dish ingredient priors or source priors. | Keep `food_ai/data/builders/build_palm_v2_db.py`; keep `build_dish_priors.py` as helper or move helper functions later. | RecipeNLG frequency extraction; PALM_V2 candidate classification; FNDDS child normalization. | Old DB upsert/materializer functions after backup. | HIGH | Single source of truth for ingredients. | Rebuild PALM_V2, validate lasagna/donut/pizza/fried rice ingredients. |
| USDA portion flow | `food_bridge/usda_portions.py`, `sync/usda_portion_loader.py`, `usda/portions.py`, `ingredient_measure_parser.py` | Old loader and lookup overlap with V2 portion priors. | `food_ai/food_bridge/ingredient_measure_parser.py` plus PALM_V2 `food_portion_priors`; optional `food_ai/usda/portions.py` for pure helpers. | Unit normalization and measure parsing. | DB loader for old `usda_portions` tables once unused. | MEDIUM | Removes plural-table confusion. | Portion lookup tests and UI smoke. |
| USDA old generic matching | `usda/service.py`, `sync/usda_ingredient_nutrition_loader.py`, `usda/embeddings.py`, `tools/build_usda_embeddings.py` | Old generic USDA matching stack. | Keep under `food_ai/usda/legacy_matching.py` or archive as old PALM flow. | `lexical_match_score`, representative FDC tests if still valuable. | Embedding fallback if no caller remains. | MEDIUM | Clear boundary between PALM_V2 and legacy. | Refactor core USDA matching tests. |
| Ingredient normalization APIs | root `ingredient_normalize.py`, `utils/ingredient_normalize.py`, `food_bridge/ingredient_normalize.py` | Multiple overlapping normalizers. | One public module plus compatibility imports. | Current PALM_V2 `normalize_ingredient` behavior; old metadata API if tests require. | Root duplicate after import scan. | HIGH | Reduces accidental behavior drift. | Run normalization tests and PALM_V2 validation. |
| Validation harnesses | `validate_palm_v2_db.py`, `smoke_test_dish_ingredient_priors.py`, `tools/run_full_audit.py`, `ui/test_diagnostic_harness.py` | Multiple audit/smoke pathways. | Keep focused `validate_palm_v2_db.py`; keep UI tests separate. | PALM_V2 correctness checks; UI smoke. | Old bridge smoke once covered. | MEDIUM | Clearer pass/fail signal. | Run validation plus Streamlit smoke. |
| CLI wrappers | `scripts/run_food_ai.py`, `scripts/run_barcode.py`, `scripts/log_meal.py`, `tools/today_cli.py`, launchers | Thin/manual entry points have mixed generations. | Keep launchers and package CLIs; archive old root/script wrappers. | `today_cli.py` if useful. | Old wrappers importing missing/legacy modules. | LOW/MEDIUM | Less confusion for new engineer. | Manual launch smoke. |

## Condense Plan

| Current scripts | Recommended final script/module | Reason to combine | Functions to keep | Functions to archive/remove | Category | Risk | Priority | Validation test |
|---|---|---|---|---|---|---|---|---|
| `build_db_dish_ingredient_priors.py`, `migrate_dish_ingredient_priors.py` | Archive under `archive/one_off_repairs/` after backup | One-off old DB mutation scripts overlap PALM_V2 builder. | None unless old DB rebuild is still required. | Old migration/upsert functions. | migration | HIGH | P1 | Confirm PALM_V2 builder/validation pass and old DB backup exists. |
| `build_dish_mass_priors.py`, `build_nutrition5k_mass_priors.py` | `food_ai/data/builders/prior_artifacts.py` or archive Nutrition5k path | Both produce mass prior artifacts. | Reliable RecipeNLG parsing helpers. | Nutrition5k if unused. | build-time | MEDIUM | P2 | Compare PALM_V2 grams for validation dish set. |
| `sync/usda_portion_loader.py`, `food_bridge/usda_portions.py` | PALM_V2 `food_portion_priors` build/read helpers | Old portion tables should not be PALM_V2 runtime dependency. | `normalize_unit`, parser support if still used. | Old DB loader. | build-time/runtime boundary | MEDIUM | P2 | Portion prior row count and serving UI smoke. |
| root `ingredient_normalize.py`, `utils/ingredient_normalize.py` | Compatibility wrapper around canonical package module | Duplicate code. | Public metadata normalizer if external callers use it. | Root duplicate. | helper | HIGH | P2 | Full normalization tests. |
| `tools/run_full_audit.py`, `smoke_test_dish_ingredient_priors.py` | Focused validation plus optional environment smoke | Overlapping audit reports. | Environment diagnostics, UI smoke. | Old prior smoke once covered. | validation | MEDIUM | P3 | `validate_palm_v2_db` and UI smoke. |
| `scripts/run_food_ai.py`, `scripts/run_barcode.py` | Package CLIs or launchers | Legacy wrappers predate refactor. | None unless users depend on command names. | Old imports/wrappers. | runtime CLI | LOW | P3 | User launcher smoke and barcode tests. |

## Removal / Archive Candidates

Archive-first is recommended. Do not directly delete high-risk files.

| Path | Reason | Evidence | Check before removing | Action | Risk |
|---|---|---|---|---|---|
| `scripts/run_food_ai.py` | Predates current package layout. | README says legacy; imports old `food_ai.infer.meal_infer`. | Ask if user still runs it manually. | ARCHIVE | LOW |
| `scripts/run_barcode.py` | Legacy wrapper. | README marks old scripts legacy. | Confirm launcher/API replacement. | ARCHIVE | LOW |
| `scripts/log_meal.py` | Standalone DB mutator duplicating package logging. | Own `init_db`/`log_meal`; not integrated with PALM_V2 bridge. | Backup DB; confirm no scheduled/manual use. | ARCHIVE | MEDIUM |
| `ingredient_normalize.py` | Root duplicate normalizer. | Similar API to `food_ai/utils/ingredient_normalize.py`. | `rg "import ingredient_normalize|from ingredient_normalize"` must be clean. | ARCHIVE | LOW |
| `food_ai/food_bridge/migrate_dish_ingredient_priors.py` | One-off migration. | Backup/schema mutation functions. | Confirm migrated DB and backup. | ARCHIVE | HIGH |
| `food_ai/food_bridge/build_db_dish_ingredient_priors.py` | Old DB prior materializer. | Overlaps PALM_V2 builder and mutates `dish_ingredient_priors`. | Confirm PALM_V2 is canonical. | ARCHIVE | HIGH |
| `food_ai/sync/usda_portion_loader.py` | Old portion table loader. | References `usda_portions`; PALM_V2 uses `food_portion_priors`. | Confirm UI no longer relies on old audit call. | ARCHIVE | MEDIUM |
| `food_ai/sync/build_usda_backbone.py` | Old USDA backbone artifact. | Used by old full audit, not PALM_V2 runtime. | Confirm old USDA service retirement. | MANUAL REVIEW | LOW |
| `food_ai/food_bridge/build_nutrition5k_*` | Experimental prior builders. | Not core PALM_V2 source priority. | Decide whether Nutrition5k remains a supported source. | MANUAL REVIEW | LOW |
| `food_ai/tools/run_full_audit.py` | Broad old audit, may mutate/copy DB. | Imports many old modules and smoke paths. | Keep if needed for environment smoke; otherwise narrow. | MANUAL REVIEW | MEDIUM |

## Recommended Target Structure

Proposed structure after staged cleanup:

```text
C:\PALM
|-- app/
|   `-- streamlit_app.py
|-- food_ai/
|   |-- config.py
|   |-- nutrition/
|   |   |-- nutrition_bridge.py
|   |   |-- dish_lookup.py
|   |   |-- ingredient_normalization.py
|   |   |-- portion_estimation.py
|   |   `-- nutrition_calculation.py
|   |-- data/
|   |   |-- builders/
|   |   |   |-- build_palm_v2_db.py
|   |   |   `-- prior_artifacts.py
|   |   |-- usda/
|   |   `-- migrations/
|   |-- models/
|   |   `-- vision_inference.py
|   |-- validation/
|   |   |-- validate_palm_v2_db.py
|   |   `-- validate_food_examples.py
|   `-- tests/
|-- shared/
|-- docs/
`-- archive/
    |-- old_experiments/
    |-- one_off_repairs/
    `-- deprecated_builders/
```

Notes:

- Do not combine UI and core bridge code.
- Do not combine destructive DB migration code with runtime code.
- Keep PALM_V2 builder separate from runtime bridge.
- If old `palm.db` support remains, name it explicitly as legacy compatibility.

## Staged Refactor Plan

### Phase 1: Documentation And No-Risk Cleanup

Objective: Make current behavior understandable without changing code.

Affected files: docs, README, launcher README.

Risk: LOW.

Benefit: New engineer can run/build/validate without guessing.

Validation steps:

- Open docs.
- Run PALM_V2 validation.
- Launch UI smoke test.

### Phase 2: Archive Obsolete Files And Consolidate Duplicate Scripts

Objective: Move one-off/legacy scripts out of active paths after backup.

Affected files: `scripts/*`, old `food_bridge` migration/build scripts, old sync loaders.

Risk: MEDIUM/HIGH depending on script.

Benefit: Fewer accidental DB mutations and stale rebuild paths.

Validation steps:

- `rg` for imports before moving.
- Run targeted tests.
- Rebuild PALM_V2.
- Validate PALM_V2.

### Phase 3: Separate Runtime, Build-Time, UI, And Migration Logic

Objective: Enforce module boundaries.

Affected files: bridge, builder, validation, UI services.

Risk: MEDIUM.

Benefit: Runtime only reads compact tables; builder owns raw USDA tables.

Validation steps:

- Check bridge does not query raw `usda_*` tables at runtime.
- Test UI ingredient exclusion/recalculation.
- Validate sample dishes.

### Phase 4: Clean DB Schema And Verify Keys/Indexes

Objective: Make PALM_V2 relationships explicit and efficient.

Affected files: builder schema definitions and validation.

Risk: MEDIUM.

Benefit: Faster lookup and fewer duplicate/ambiguous rows.

Validation steps:

- Schema diff.
- Row-count checks.
- Duplicate primary/unique-key checks.
- Barcode latest-publication validation.

### Phase 5: Add Validation Tests

Objective: Turn audit expectations into repeatable tests.

Affected files: validation module and unit tests.

Risk: LOW/MEDIUM.

Benefit: Protects ingredient logic from regression.

Validation steps:

- Run dish set: lasagna, spaghetti, glazed donut, pizza, hamburger, salad, fried rice, pancakes, omelet, macaroni and cheese, taco, burrito, chicken curry, grilled cheese sandwich, chocolate cake.
- Confirm no composites in user-facing ingredient priors.
- Confirm grams sum near 100g before exclusions.

### Phase 6: Improve Ingredient Logic And Nutrition Explainability

Objective: Improve user trust in ingredient tables and nutrition totals.

Affected files: PALM_V2 builder, bridge, formatter/UI, validation.

Risk: MEDIUM/HIGH because nutrition correctness is user-facing.

Benefit: Believable ingredients, explainable grams, USDA-backed nutrient contributions.

Validation steps:

- Compare sample ingredient tables before/after.
- Check macro plausibility by dish family.
- Verify generic form constraints: egg not dried, milk not powder, cooked pasta not dry pasta.

## Biggest Architecture Risks

- PALM_V2 and legacy PALM paths can both mutate similarly named tables.
- Ingredient normalization behavior is spread across multiple modules.
- Old USDA matching can conflict with PALM_V2 source priority.
- Some scripts have hardcoded paths or implicit DB defaults.
- Raw USDA data is huge, making accidental runtime raw-table queries expensive.
- One-off migration scripts lack formal migration tracking.
- UI can hide renormalization if original and adjusted ingredient grams are not both exposed.

## Best 5 Consolidation Opportunities

1. Merge/archive old dish prior DB materialization scripts behind PALM_V2 builder.
2. Collapse root and utils ingredient normalizer duplicates into one documented API.
3. Retire old `usda_portions` loaders in favor of PALM_V2 `food_portion_priors`.
4. Split `test_refactor_core.py` into smaller focused test files while preserving coverage.
5. Fold old smoke/audit checks into `validate_palm_v2_db.py` plus a small UI smoke test.

## Scripts That Should Remain Separate

- `food_ai/ui/app.py` and UI components should remain separate from nutrition bridge.
- `food_ai/food_bridge/nutrition_bridge.py` should remain separate from DB builder.
- `food_ai/data/builders/build_palm_v2_db.py` should remain separate from validation.
- `food_ai/vision/train_classifier.py` should remain separate from `predict_dish.py`.
- Barcode product lookup should remain separate from generic dish nutrition.
