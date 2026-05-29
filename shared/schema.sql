-- =========================
-- PALM Food AI - Schema v1
-- =========================

PRAGMA foreign_keys = ON;

-- ---------
-- PRODUCTS
-- ---------
CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT,
    product_name TEXT NOT NULL,
    category TEXT,
    source TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
	raw_nutrition_json TEXT
);

-- ---------
-- BARCODES
-- ---------
CREATE TABLE IF NOT EXISTS barcodes (
    barcode TEXT PRIMARY KEY,
    product_id INTEGER NOT NULL,
    format TEXT,
    verified INTEGER DEFAULT 0,
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

-- ---------------
-- SERVING SIZES
-- ---------------
CREATE TABLE IF NOT EXISTS serving_sizes (
    serving_id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    serving_name TEXT,
    grams REAL NOT NULL,
    is_default INTEGER DEFAULT 0,
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

-- ----------------
-- NUTRITION FACTS
-- Per 100g values
-- ----------------
CREATE TABLE nutrition_facts (
    product_id INTEGER PRIMARY KEY,
    calories REAL,
    protein_g REAL,
    total_fat_g REAL,
    saturated_fat_g REAL,
    trans_fat_g REAL,
    total_carbs_g REAL,
    fiber_g REAL,
    total_sugars_g REAL,
    added_sugars_g REAL,
    sodium_mg REAL,
    cholesterol_mg REAL,
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
-- -----------------
-- CONSUMPTION LOGS
-- -----------------
CREATE TABLE IF NOT EXISTS consumption_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode TEXT,
    grams_consumed REAL NOT NULL,
    consumed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (barcode) REFERENCES barcodes(barcode)
);
-- ----------------
-- UNKNOWN BARCODES
-- ----------------
CREATE TABLE IF NOT EXISTS unknown_barcodes (
    barcode TEXT PRIMARY KEY,
    first_seen TEXT DEFAULT CURRENT_TIMESTAMP
);
-- -------
-- INDEXES
-- -------
CREATE INDEX IF NOT EXISTS idx_barcodes_product_id
    ON barcodes(product_id);

CREATE INDEX IF NOT EXISTS idx_logs_consumed_at
    ON consumption_logs(consumed_at);
------------------
-- MICRO NUTRIENTS
------------------
CREATE TABLE nutrition_micros (
    product_id INTEGER PRIMARY KEY,
    vitamin_d_mcg REAL,
    calcium_mg REAL,
    iron_mg REAL,
    potassium_mg REAL,
    caffeine_mg REAL,
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
----------------
-- GENERIC FOODS
----------------
CREATE TABLE generic_foods (
    food_id        INTEGER PRIMARY KEY,
    food_name      TEXT NOT NULL,        -- "Rice, cooked"
    ingredient     TEXT NOT NULL,        -- "rice"
    prep_state     TEXT,                 -- raw, cooked, roasted
    food_category  TEXT,
    source         TEXT DEFAULT 'USDA',
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
----------------------
-- GENERIC FOODS NUTR.
----------------------
CREATE TABLE generic_food_nutrition (
    food_id              INTEGER PRIMARY KEY,
    -- === ENERGY ===
    calories             REAL,
    caffeine_mg          REAL,
    -- === MACROS ===
    protein_g            REAL,
    total_fat_g          REAL,
    saturated_fat_g      REAL,
    trans_fat_g          REAL,
    total_carbs_g        REAL,
    fiber_g              REAL,
    total_sugars_g       REAL,
    added_sugars_g       REAL,
    -- === MICROS ===
    sodium_mg            REAL,
    potassium_mg         REAL,
    calcium_mg           REAL,
    iron_mg              REAL,
    magnesium_mg         REAL,
    zinc_mg              REAL,
    phosphorus_mg        REAL,
    vitamin_a_mcg        REAL,
    vitamin_c_mg         REAL,
    vitamin_d_mcg        REAL,
    vitamin_e_mg         REAL,
    vitamin_k_mcg        REAL,
    vitamin_b12_mcg      REAL,
    folate_mcg           REAL,
    cholesterol_mg       REAL,
    FOREIGN KEY(food_id) REFERENCES generic_foods(food_id)
);
-------------------
-- UNIT CONVERSIONS
-------------------
CREATE TABLE food_units (
    unit_id       INTEGER PRIMARY KEY,
    food_id       INTEGER,
    unit_name     TEXT NOT NULL,     -- cup, tbsp, slice, medium
    grams         REAL NOT NULL,     -- exact weight for THIS food
    source        TEXT DEFAULT 'USDA',
    confidence    REAL DEFAULT 1.0,
    FOREIGN KEY(food_id) REFERENCES generic_foods(food_id)
);

CREATE TABLE generic_ingredient_nutrition (
    ingredient          TEXT PRIMARY KEY,

    -- === ENERGY ===
    calories             REAL,
    caffeine_mg          REAL,

    -- === MACROS ===
    protein_g            REAL,
    fat_g                REAL,
    carbs_g              REAL,
    fiber_g              REAL,
    sugar_g              REAL,

    -- === FAT BREAKDOWN ===
    sat_fat_g            REAL,
    mono_fat_g           REAL,
    poly_fat_g           REAL,

    -- === MICROS ===
    sodium_mg            REAL,
    potassium_mg         REAL,
    calcium_mg           REAL,
    iron_mg              REAL,
    magnesium_mg         REAL,
    zinc_mg              REAL,
    phosphorus_mg        REAL,
    cholesterol_mg       REAL,

    -- === METADATA ===
    coverage             INTEGER,          -- nutrient completeness score
    fdc_id               INTEGER,
    description          TEXT,
    source               TEXT DEFAULT 'USDA_FDC_FOUNDATION'
);

CREATE TABLE IF NOT EXISTS ingredient_aliases (
    alias                 TEXT PRIMARY KEY,
    canonical_ingredient  TEXT NOT NULL,
    notes                 TEXT
);

-------------------------
-- USDA PORTION LOOKUPS
-------------------------
CREATE TABLE IF NOT EXISTS usda_measure_units (
    measure_unit_id INTEGER PRIMARY KEY,
    unit_name TEXT NOT NULL,
    normalized_unit TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usda_food_portions (
    fdc_id INTEGER NOT NULL,
    amount REAL,
    measure_unit_id INTEGER,
    unit_name TEXT,
    normalized_unit TEXT,
    portion_description TEXT,
    modifier TEXT,
    gram_weight REAL NOT NULL,
    data_points INTEGER,
    PRIMARY KEY (fdc_id, amount, measure_unit_id, portion_description, modifier, gram_weight)
);

CREATE INDEX IF NOT EXISTS idx_usda_food_portions_lookup
    ON usda_food_portions(fdc_id, normalized_unit);

----------------------------
-- DISH INGREDIENT PRIORS
----------------------------
CREATE TABLE IF NOT EXISTS dishes (
    dish_id INTEGER PRIMARY KEY AUTOINCREMENT,
    food101_class_idx INTEGER UNIQUE,
    food101_label TEXT UNIQUE NOT NULL,
    display_name TEXT,
    source TEXT DEFAULT 'food101',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dish_ingredient_priors (
    prior_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dish_id INTEGER NOT NULL,
    canonical_ingredient TEXT NOT NULL,
    ingredient_norm_key TEXT,
    fdc_id INTEGER,
    usda_description TEXT,
    prep_state TEXT,
    grams_mean REAL,
    grams_mode REAL,
    grams_median REAL,
    grams_std REAL,
    grams_default REAL,
    grams_per_100g_dish REAL,
    portion_method TEXT,
    pct_recipes_present REAL,
    ingredient_rank INTEGER,
    n_recipes INTEGER,
    n_weight_observations INTEGER,
    default_include INTEGER DEFAULT 1,
    confidence REAL,
    source TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dish_id) REFERENCES dishes(dish_id),
    UNIQUE (dish_id, canonical_ingredient, fdc_id)
);

CREATE TABLE IF NOT EXISTS dish_portion_priors (
    dish_id INTEGER PRIMARY KEY,
    dish_grams_mean REAL,
    serving_grams_median REAL,
    serving_grams_mode REAL,
    n_recipes INTEGER,
    confidence REAL,
    source TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dish_id) REFERENCES dishes(dish_id)
);

CREATE TABLE IF NOT EXISTS recipe_ingredient_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dish_id INTEGER NOT NULL,
    recipe_id TEXT,
    recipe_source TEXT,
    raw_ingredient_text TEXT,
    canonical_ingredient TEXT,
    ingredient_norm_key TEXT,
    parsed_quantity REAL,
    parsed_unit TEXT,
    parsed_grams REAL,
    servings REAL,
    grams_per_serving REAL,
    fdc_id INTEGER,
    usda_description TEXT,
    parse_confidence REAL,
    match_confidence REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dish_id) REFERENCES dishes(dish_id)
);

CREATE TABLE IF NOT EXISTS inferred_dish_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dish_id INTEGER,
    dish_label TEXT NOT NULL,
    grams_consumed REAL,
    serving_estimate_grams REAL,
    selected_ingredients_json TEXT,
    excluded_ingredients_json TEXT,
    nutrition_snapshot_json TEXT NOT NULL,
    ingredient_snapshot_json TEXT NOT NULL,
    model_confidence REAL,
    bridge_confidence REAL,
    consumed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (dish_id) REFERENCES dishes(dish_id)
);

CREATE INDEX IF NOT EXISTS idx_dish_ingredient_priors_dish_rank
    ON dish_ingredient_priors(dish_id, ingredient_rank);

CREATE INDEX IF NOT EXISTS idx_dish_ingredient_priors_fdc
    ON dish_ingredient_priors(fdc_id);

CREATE INDEX IF NOT EXISTS idx_recipe_observations_dish
    ON recipe_ingredient_observations(dish_id);

CREATE INDEX IF NOT EXISTS idx_inferred_dish_logs_consumed_at
    ON inferred_dish_logs(consumed_at);
