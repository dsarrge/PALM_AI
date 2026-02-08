# -*- coding: utf-8 -*-
"""
Created on Mon Dec 29 12:19:46 2025

@author: dsarr

Recipe → embedding export
"""

from pathlib import Path
import json
import numpy as np
import pandas as pd
from tqdm import tqdm
from sentence_transformers import SentenceTransformer

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR  = BASE_DIR.parent / "artifacts"
DATA_CSV = Path("C:/PALM/data/RecipeNLG_dataset.csv")
OUT_DIR.mkdir(exist_ok=True)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 5000
BATCH_SIZE = 128

model = SentenceTransformer(MODEL_NAME)  # CPU only (intentional)

embeddings = []
recipe_ids = []
seen = 0

for chunk in tqdm(pd.read_csv(DATA_CSV, chunksize=CHUNK_SIZE)):
    texts = []
    ids   = []

    for _, row in chunk.iterrows():
        title = row.get("title")
        ingredients = row.get("ingredients")

        if pd.isna(title) or pd.isna(ingredients):
            continue

        texts.append(f"{title} | {ingredients}")
        ids.append(seen)
        seen += 1

    if not texts:
        continue

    emb = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=True
    )

    embeddings.append(emb)
    recipe_ids.extend(ids)

embeddings = np.vstack(embeddings)

np.save(OUT_DIR / "recipe_embeddings.npy", embeddings)

with open(OUT_DIR / "recipe_ids.json", "w", encoding="utf-8") as f:
    json.dump(recipe_ids, f)

manifest = {
    "dataset": "RecipeNLG (Kaggle CSV)",
    "model": MODEL_NAME,
    "recipes_used": len(recipe_ids),
    "embedding_dim": embeddings.shape[1],
    "chunk_size": CHUNK_SIZE,
    "batch_size": BATCH_SIZE,
    "device": "cpu"
}

with open(OUT_DIR / "recipe_manifest.json", "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)

print("✅ Recipe embeddings complete. Safe to delete the CSV.")
