# PALM Streamlit Tester

Local desktop testing UI for the PALM food classifier and food bridge.

## What this tester actually uses

The maintained test flow is:

- `food_ai/ui/app.py`
- `food_ai/ui/services/inference_service.py`
- `food_ai/vision/predict_dish.py`
- `food_ai/vision/backends/pytorch_backend.py` or `food_ai/vision/backends/onnx_backend.py`
- `food_ai/food_bridge/nutrition_bridge.py`
- `artifacts/food_bridge/dish_priors.json`
- `shared/palm.db` table `generic_ingredient_nutrition`

The UI is diagnostic and read-only for inference. It does not write to the PALM DB during image inference.

## Launch from palm-gpu311

Use the intended GPU-enabled environment instead of the system Python:

```bash
conda activate palm-gpu311
python -c "from food_ai.runtime_diagnostics import get_runtime_diagnostics; import json; print(json.dumps(get_runtime_diagnostics('palm-gpu311'), indent=2))"
python -m streamlit run C:/PALM/food_ai/ui/app.py
```

## Build food bridge priors

RecipeNLG is used offline to build dish-to-ingredient priors. The UI does not scan RecipeNLG at inference time.

```bash
python -m food_ai.food_bridge.build_dish_priors
python -m food_ai.food_bridge.nutrition_bridge --dish pizza --confidence 0.85
```

## What the UI writes locally

Inference stays read-only, but the tester may write local artifacts under `artifacts/ui/`:

- uploaded or copied sample images: `artifacts/ui/uploads/`
- optional saved test logs: `artifacts/ui/test_logs/`
- optional run reports: `artifacts/ui/run_reports/`

## Recommended manual cases

Use the built-in sample panel and try at least these:

- `Waffles With Berries`: clean baseline plated-food case
- `Breakfast Sandwich Close Crop`: difficult crop / partial-food case
- `Salmon Plate With Sides`: multi-component plated-food case
- `Meatballs In Sauce`: homemade-style dish case
- `Packaged Bar Back Panel`: packaging-only false-positive check
- `Empty Table Setting`: non-food graceful-failure check
- `Texture Noise`: hard non-food failure check

## What to inspect

For each run, inspect:

- stage 1 input image preview
- stage 2 pipeline status labels and warnings
- stage 3 dish classifier candidates and final dish
- stage 4 dish-prior ingredient ranking and USDA-backed normalization
- stage 5 estimated nutrition per 100g from dish ingredient priors
- stage 6 raw JSON, missing artifacts, bridge status, and runtime diagnostics

## Notes on current behavior

- there is no dedicated food / non-food gate in the current maintained pipeline, so the UI surfaces that explicitly
- if classifier checkpoints are missing, the app shows the classifier error instead of hiding it
- if `dish_priors.json` is missing, the nutrition bridge returns a pending status with the build command
- nutrition output is an ingredient-prior estimate per 100g, not a final portion-estimated plated meal result
