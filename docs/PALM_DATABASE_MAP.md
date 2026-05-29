# PALM Database Map

Audit target: `C:\PALM\shared\PALM_V2.db`

Inspection method: SQLite schema, indexes, row counts, and small samples. No large data files were read row-by-row.

## Database Files

| Path | Role | Status |
|---|---|---|
| `shared/PALM_V2.db` | Current PALM_V2 runtime/build database. | ACTIVE |
| `shared/palm.db` | Legacy working database. Must not be overwritten by PALM_V2 work. | ACTIVE/LEGACY |
| `shared/schema.sql` | Base schema for old `palm.db` flow. | LEGACY/SUPPORTING |
| `shared/smoke*.db` | Smoke/test databases. | GENERATED |
| `shared/audit/` | Audit artifacts. | GENERATED |

## PALM_V2 Tables And Row Counts

### Compact Runtime Tables

| Table | Rows | Runtime role |
|---|---:|---|
| `canonical_foods` | 13,320 | App-facing canonical ingredients/foods. |
| `canonical_food_aliases` | 25,147 | Alias lookup for canonical foods. |
| `canonical_food_nutrients` | 13,320 | Per-100g USDA-backed nutrients. |
| `food_portion_priors` | 36,607 | Portion gram weights from USDA portions. |
| `dish_labels` | 101 | Dish key/display label registry. |
| `dish_aliases` | 101 | Dish alias lookup. |
| `dish_ingredient_priors` | 1,837 | User-facing true ingredient priors. |
| `dish_variant_evidence` | 811 | Debug/evidence parent dish variants and rejected/ambiguous candidates. |
| `nutrition_sources` | 13,320 | Source/evidence metadata for canonical nutrition. |
| `products` | 465,110 | Branded product fallback records. |
| `barcodes` | 465,110 | UPC/barcode to product mapping. |
| `serving_sizes` | 465,043 | Product serving-size metadata. |
| `consumption_logs` | 0 | Runtime meal logging target. |
| `meal_log_ingredients` | 0 | Ingredient-level meal contribution target. |
| `unknown_foods` | 0 | Runtime unknown generic food capture. |
| `unknown_barcodes` | 0 | Runtime unknown barcode capture. |
| `user_food_corrections` | 0 | Future user correction store. |

### Raw USDA Mirror Tables

| Table | Rows | Source semantics |
|---|---:|---|
| `usda_food` | 2,101,279 | USDA food identity; `fdc_id`, `data_type`, `description`, `food_category_id`, `publication_date`. |
| `usda_food_category` | 28 | Food category labels. |
| `usda_food_nutrient` | 27,195,013 | Nutrient amount per 100g food. |
| `usda_nutrient` | 477 | Nutrient ID/name/unit/nutrient number map. |
| `usda_food_nutrient_derivation` | 80 | Derivation metadata. |
| `usda_food_nutrient_source` | 10 | Nutrient source metadata. |
| `usda_food_portion` | 47,446 | Portion rows; `gram_weight` is portion gram weight. |
| `usda_measure_unit` | 123 | Portion measure unit names. |
| `usda_foundation_food` | 395 | Foundation membership table. |
| `usda_sr_legacy_food` | 7,793 | SR Legacy membership table. |
| `usda_survey_fndds_food` | 5,432 | FNDDS/survey membership table. |
| `usda_input_food` | 18,584 | FNDDS child ingredient rows; `gram_weight` is child ingredient weight inside parent food. |
| `usda_fndds_ingredient_nutrient_value` | 275,535 | FNDDS ingredient nutrient values. |
| `usda_fndds_derivation` | 49 | FNDDS derivation metadata. |
| `usda_wweia_food_category` | 172 | WWEIA category metadata. |
| `usda_branded_food` | 1,999,950 | Branded product records for barcode/product fallback. |
| `usda_import_manifest` | 16 | Raw import evidence and schema hashes. |

## Key Schema Notes

`canonical_foods`:

- Primary key: `canonical_food_id`.
- Stores `canonical_name`, `prep_state`, `food_role`, `preferred_fdc_id`, `preferred_source`, category, confidence, timestamps.
- `canonical_food_id` is PALM's app-facing key; `preferred_fdc_id` is source evidence.

`canonical_food_nutrients`:

- Primary key: `canonical_food_id`.
- Per-100g values include calories, protein, fat, saturated fat, carbs, fiber, sugar, sodium, cholesterol, calcium, iron, potassium.
- `source_fdc_id` points back to USDA evidence.

`dish_ingredient_priors`:

- Primary key: `dish_prior_id`.
- Unique key: `(dish_key, canonical_ingredient_id, source)`.
- Foreign keys declared to `dish_labels(dish_key)` and `canonical_foods(canonical_food_id)`.
- Stores mean/median/mode grams per 100g dish, prevalence, recipe count, source, ingredient role, confidence.
- This is the user-facing ingredient table.

`dish_variant_evidence`:

- Primary key: `evidence_id`.
- Stores FNDDS parent variants, composite candidates, ambiguous rows, rejected candidates, and reasons.
- This should be debug/evidence only, not displayed as an ingredient table.

`products`, `barcodes`, `serving_sizes`:

- `products.source_fdc_id` is unique.
- `barcodes.barcode` is primary key.
- `barcodes.product_id -> products.product_id`.
- `serving_sizes.product_id -> products.product_id`.
- PALM_V2 validation checks that each UPC selects the latest USDA branded `food.publication_date`.

`meal_log_ingredients`:

- Intended to store ingredient-level meal snapshots.
- Includes original/used grams and macro contribution columns.
- This is the table the UI can rely on later for exclusion/recalculation history.

## Indexes

Important indexes observed:

| Index | Table/columns |
|---|---|
| `idx_dish_ingredient_priors_dish` | `dish_ingredient_priors(dish_key)` |
| `idx_dish_variant_evidence_dish` | `dish_variant_evidence(dish_key)` |
| `idx_dish_aliases_dish` | `dish_aliases(dish_key)` |
| `idx_canonical_food_aliases_food` | `canonical_food_aliases(canonical_food_id)` |
| `idx_food_portion_priors_food` | `food_portion_priors(canonical_food_id)` |
| `idx_barcodes_product` | `barcodes(product_id)` |
| `idx_serving_sizes_product` | `serving_sizes(product_id)` |
| `idx_meal_log_ingredients_log` | `meal_log_ingredients(log_id)` |
| `idx_usda_food_fdc_id` | `usda_food(fdc_id)` |
| `idx_usda_food_type` | `usda_food(data_type)` |
| `idx_usda_food_nutrient_food_nutrient` | `usda_food_nutrient(fdc_id, nutrient_id)` |
| `idx_usda_food_nutrient_nutrient_food` | `usda_food_nutrient(nutrient_id, fdc_id)` |
| `idx_usda_food_portion_food` | `usda_food_portion(fdc_id)` |
| `idx_usda_input_food_food` | `usda_input_food(fdc_id)` |
| `idx_usda_input_food_input` | `usda_input_food(fdc_id_of_input_food)` |
| `idx_usda_branded_food_upc` | `usda_branded_food(gtin_upc)` |

Missing or worth considering later:

- Index on `canonical_foods(preferred_fdc_id)`.
- Index on `canonical_food_aliases(alias_name)` if runtime lookup does alias search directly.
- Index on `dish_aliases(alias_name)` if dish lookup uses alias text frequently.
- Composite index on `products(source_fdc_id, publication_date)` is probably unnecessary because `source_fdc_id` is unique, but barcode validation can be expensive against raw branded rows.

## Likely Relationships

SQLite declares some foreign keys, but not all relationships are enforced. Treat these as logical relationships:

| Relationship | Meaning |
|---|---|
| `dish_labels.dish_key -> dish_aliases.dish_key` | Label to aliases. |
| `dish_labels.dish_key -> dish_ingredient_priors.dish_key` | Dish to user-facing ingredient priors. |
| `dish_labels.dish_key -> dish_variant_evidence.dish_key` | Dish to debug/evidence variants. |
| `dish_ingredient_priors.canonical_ingredient_id -> canonical_foods.canonical_food_id` | Ingredient prior to canonical USDA-backed food. |
| `canonical_foods.canonical_food_id -> canonical_food_nutrients.canonical_food_id` | Canonical food to per-100g nutrients. |
| `canonical_foods.canonical_food_id -> food_portion_priors.canonical_food_id` | Canonical food to portion priors. |
| `canonical_foods.preferred_fdc_id -> usda_food.fdc_id` | Canonical source evidence. |
| `canonical_food_nutrients.source_fdc_id -> usda_food.fdc_id` | Nutrient source evidence. |
| `barcodes.product_id -> products.product_id` | UPC to product. |
| `serving_sizes.product_id -> products.product_id` | Product to serving sizes. |
| `meal_log_ingredients.log_id -> consumption_logs.log_id` | Meal snapshot to ingredient contributions. |

## Runtime Joins

Dish label / alias lookup:

```sql
SELECT dish_key FROM dish_labels WHERE dish_key = ? OR display_name = ?;
SELECT dish_key FROM dish_aliases WHERE alias_name = ?;
```

Dish to ingredient priors:

```sql
SELECT *
FROM dish_ingredient_priors
WHERE dish_key = ?
ORDER BY confidence_score DESC;
```

Ingredient prior to canonical food and nutrition:

```sql
SELECT dip.*, cf.canonical_name, cf.prep_state, cfn.*
FROM dish_ingredient_priors dip
JOIN canonical_foods cf
  ON cf.canonical_food_id = dip.canonical_ingredient_id
JOIN canonical_food_nutrients cfn
  ON cfn.canonical_food_id = cf.canonical_food_id
WHERE dip.dish_key = ?;
```

Ingredient grams to nutrient contribution:

```text
nutrient_total = ingredient_grams * nutrient_per_100g / 100
```

Portion lookup:

```sql
SELECT *
FROM food_portion_priors
WHERE canonical_food_id = ?
ORDER BY confidence_score DESC;
```

Barcode lookup:

```sql
SELECT b.*, p.*, s.*
FROM barcodes b
JOIN products p ON p.product_id = b.product_id
LEFT JOIN serving_sizes s ON s.product_id = p.product_id
WHERE b.barcode = ?;
```

## Table Access By Script

| Tables | Main readers/writers |
|---|---|
| PALM_V2 raw `usda_*` | Written by `food_ai/data/builders/build_palm_v2_db.py`; read by validation and build-time joins. |
| `canonical_foods`, `canonical_food_nutrients`, `canonical_food_aliases` | Written by builder; read by `nutrition_bridge.py`, validation, tests. |
| `dish_labels`, `dish_aliases`, `dish_ingredient_priors`, `dish_variant_evidence` | Written by builder; read by bridge, validation, UI formatting, tests. |
| `products`, `barcodes`, `serving_sizes` | Written by builder and barcode/OpenFoodFacts services; read by barcode service, log tests, validation. |
| `consumption_logs`, `meal_log_ingredients` | Intended runtime logging targets; read by analytics/daily totals. |
| Legacy `generic_*`, `core_ingredients`, `usda_embeddings` | Mostly old `palm.db`/artifact flow; not core PALM_V2 runtime tables. |

## Duplicates Or Risky Tables

The PALM_V2 DB currently does not contain the earlier empty duplicate plural raw tables `usda_food_portions` or `usda_measure_units`. The canonical raw mirror tables are singular:

- `usda_food_portion`
- `usda_measure_unit`

Risk to watch: older scripts still reference names like `usda_portions`, `usda_food_portions_count`, `usda_measure_units_count`, or older PALM DB schemas. Those should not be used as PALM_V2 runtime dependencies.

## Sample Runtime State

Representative sample inspection showed:

- `dish_ingredient_priors` for `lasagna` contains true ingredients such as cooked pasta, egg, ground beef, onion, garlic, and parmesan.
- `dish_variant_evidence` for `lasagna` contains parent/composite evidence such as restaurant/frozen/canned lasagna rows and rejected composite input foods.
- `dish_ingredient_priors` for `fried_rice` contains common ingredients such as soy sauce, egg, garlic, rice, onion, oil, green onion, carrot, and sesame oil, plus lower-confidence fallback proteins.

This matches the intended separation: evidence/debug rows are not user-facing ingredients.
