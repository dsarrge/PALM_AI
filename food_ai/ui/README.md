# PALM Streamlit UIs

Local desktop Streamlit UIs for the PALM food classifier and food bridge.

## Diagnostic Tester

The maintained test flow is:

- `food_ai/ui/app.py`
- `food_ai/ui/services/inference_service.py`
- `food_ai/vision/predict_dish.py`
- `food_ai/vision/backends/pytorch_backend.py` or `food_ai/vision/backends/onnx_backend.py`
- `food_ai/food_bridge/nutrition_bridge.py`
- `artifacts/food_bridge/dish_priors.json`
- `shared/palm.db` table `generic_ingredient_nutrition`

The tester is diagnostic infrastructure. It can write local JSON test artifacts under `artifacts/ui/test_logs/`, but it does not write reviewed meal logs to the PALM DB.

Launch the tester from the intended GPU-enabled environment:

```bash
conda activate palm-gpu311
python -c "from food_ai.runtime_diagnostics import get_runtime_diagnostics; import json; print(json.dumps(get_runtime_diagnostics('palm-gpu311'), indent=2))"
python -m streamlit run C:/PALM/food_ai/ui/app.py
```

## Production Meal Logger

The production UI lives at `food_ai/ui/production_app.py`.

It reuses the same upload, inference, ingredient editor, nutrition panel, runtime diagnostics, and file utilities as the tester, but adds a DB-backed meal logging workflow through `food_ai/ui/services/meal_log_service.py`.

Launch it with:

```bash
python -m streamlit run C:/PALM/food_ai/ui/production_app.py
```

On the Windows PALM workstation, use:

```bash
launchers/run_palm_meal_logger.bat
```

The production app creates the `meal_logs` table on demand in the selected SQLite database. By default, that is `shared/PALM_V2.db` when present, otherwise `shared/palm.db`.

Each saved meal row includes:

- image path
- created and optional eaten timestamps
- corrected dish name
- confidence
- serving weight in grams
- scaled calories and nutrient totals
- per-100g nutrition JSON
- included and excluded ingredient JSON
- notes
- raw inference payload JSON

## Build food bridge priors

RecipeNLG is used offline to build dish-to-ingredient priors. The UIs do not scan RecipeNLG at inference time.

```bash
python -m food_ai.food_bridge.build_dish_priors
python -m food_ai.food_bridge.nutrition_bridge --dish pizza --confidence 0.85
```

## Recommended manual tester cases

Use the built-in sample panel in `app.py` and try at least these:

- `Waffles With Berries`: clean baseline plated-food case
- `Breakfast Sandwich Close Crop`: difficult crop / partial-food case
- `Salmon Plate With Sides`: multi-component plated-food case
- `Meatballs In Sauce`: homemade-style dish case
- `Packaged Bar Back Panel`: packaging-only false-positive check
- `Empty Table Setting`: non-food graceful-failure check
- `Texture Noise`: hard non-food failure check

## Notes on current behavior

- there is no dedicated food / non-food gate in the current maintained pipeline, so the UI surfaces that explicitly
- if classifier checkpoints are missing, the app shows the classifier error instead of hiding it
- if `dish_priors.json` is missing, the nutrition bridge returns a pending status with the build command
- nutrition output is an ingredient-prior estimate per 100g before the production app scales it to the user-entered serving size
