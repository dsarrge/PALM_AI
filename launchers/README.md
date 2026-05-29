# PALM Launchers

These Windows launcher files live here so PALM can always run with the intended interpreter:

`C:\Users\dsarr\AppData\Local\spyder-6\envs\palm-gpu311\python.exe`

Using the full interpreter path avoids broken `conda activate` and PowerShell activation issues. Each launcher checks that the interpreter and target script exist before it runs.

## Launchers

- `run_palm_ui.bat`
  - starts the local Streamlit tester
- `run_food_smoke_test.bat`
  - runs a food-vision smoke test against `food_ai/tests/food_1.jpg`
  - writes JSON output to `artifacts/vision/smoke/smoke_result.json`
- `rebuild_usda_embeddings.bat`
  - rebuilds `food_ai/data/usda_embeddings.pkl` using `food_ai.tools.build_usda_embeddings`
- `run_palm_runtime_diagnostics.bat`
  - prints interpreter, Python version, torch/CUDA, GPU name, sentence_transformers, transformers, and streamlit status
- `palm_gpu311_shell.bat`
  - opens a `cmd` shell in `C:\PALM` and defines `py` as the pinned PALM interpreter for ad hoc commands

## Updating the Interpreter Path

If the environment moves later, update the `PYTHON_EXE=` line in each `.bat` file to the new interpreter path.

## Why This Works

These launchers do not rely on shell activation state. They call the exact Python executable directly, so the PALM app and rebuild scripts use the correct environment every time.
