# -*- coding: utf-8 -*-
"""
Created on Thu Jan  1 20:00:42 2026

@author: dsarr

food_ai/tools/build_recipe_manifest.py
"""
import re
import json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

ARTIFACTS = Path("C:/PALM/artifacts")
DATA_CSV = Path("C:/PALM/food_ai/data/RecipeNLG_dataset.csv")

DISH_LABELS = ARTIFACTS / "dish_labels_v2.json"
OUT_MANIFEST = ARTIFACTS / "dish_recipe_manifest.json"

def build_recipe_manifest():
    # Load dishes
    with open(DISH_LABELS, "r", encoding="utf-8") as f:
        dishes = json.load(f)

    # Load dataset
    df = pd.read_csv(DATA_CSV, usecols=["title", "ingredients"])
    df["title"] = df["title"].astype(str).str.lower()

    manifest = {
        "version": "v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "normalizer": "ingredient_normalizer_v1",
        "dishes": []
    }
    
    for dish_key in dishes:
        dish_name = dish_key.replace("_", " ")
        tokens = dish_name.split()
        pattern = ".*".join(map(re.escape, tokens))
        
        matched = df[
            df["title"].str.contains(pattern, regex=True)
        ]
        
        if matched.empty:
            continue

        manifest["dishes"].append({
            "dish_key": dish_key,
            "recipe_ids": matched.index.tolist(),
            "recipe_count": len(matched),
        })
    
        print(f"[OK] {dish_key}: {len(matched)} recipes")

    with open(OUT_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n[SAVED] {OUT_MANIFEST}")

if __name__ == "__main__":
    build_recipe_manifest()
