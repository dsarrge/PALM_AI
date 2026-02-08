# -*- coding: utf-8 -*-
"""
Created on Thu Jan  1 18:25:22 2026

@author: dsarr

food_ai/tools/collapse_recipe_manifest.py
Collapse recipe embeddings into a dish-level recipe manifest.

Inputs:
  artifacts/dish_manifest.json
  artifacts/recipe_embeddings.npy
  artifacts/recipe_ids.json

Output:
  artifacts/dish_recipe_manifest.json
"""

import json
import numpy as np
from pathlib import Path
from datetime import datetime
from sentence_transformers import SentenceTransformer

# PATHS
BASE_DIR  = Path(__file__).resolve().parents[1]   # food_ai/
ARTIFACTS = BASE_DIR.parent / "artifacts"

DISH_LABELS   = ARTIFACTS / "dish_labels_v2.json"
RECIPE_EMB    = ARTIFACTS / "recipe_embeddings.npy"
RECIPE_IDS    = ARTIFACTS / "recipe_ids.json"

OUT_PATH = ARTIFACTS / "dish_recipe_manifest.json"

# PARAMETERS
SIM_THRESHOLD = 0.65
MAX_RECIPES   = 50_000
MODEL_NAME    = "sentence-transformers/all-MiniLM-L6-v2"

# MAIN
def main():
    print("[INFO] Loading artifacts")

    with open(DISH_LABELS, "r", encoding="utf-8") as f:
        dish_labels = json.load(f)

    recipe_embs = np.load(RECIPE_EMB)
    with open(RECIPE_IDS, "r", encoding="utf-8") as f:
        recipe_ids = json.load(f)

    print("[INFO] Loading embedding model")
    model = SentenceTransformer(MODEL_NAME)

    out = {
        "version": "2.0",
        "created_at": datetime.utcnow().isoformat(),
        "model": MODEL_NAME,
        "embedding_dim": recipe_embs.shape[1],
        "num_dishes": len(dish_labels),
        "dishes": []
    }

    print("[INFO] Collapsing recipes → dishes")

    for dish_key in dish_labels:
        dish_name = dish_key.replace("_", " ").title()

        dish_vec = model.encode(
            dish_name,
            normalize_embeddings=True,
            convert_to_numpy=True
        )

        scores = recipe_embs @ dish_vec
        idx = np.where(scores >= SIM_THRESHOLD)[0]

        if len(idx) > MAX_RECIPES:
            idx = idx[np.argsort(scores[idx])[-MAX_RECIPES:]]

        out["dishes"].append({
            "dish_key": dish_key,
            "num_recipes": int(len(idx)),
            "recipe_ids": [recipe_ids[i] for i in idx],
            "mean_similarity": float(scores[idx].mean()) if len(idx) else 0.0
        })

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(f"[OK] Dish recipe manifest written → {OUT_PATH}")

# ENTRY
if __name__ == "__main__":
    main()
