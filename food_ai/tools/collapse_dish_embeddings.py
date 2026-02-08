# -*- coding: utf-8 -*-
"""
Created on Thu Jan  1 13:32:53 2026

@author: dsarr

Collapse per-image dish embeddings into per-dish centroids.
Purpose:
- Remove duplicate dish labels
- Improve similarity margins
- Stabilize confidence scores

Input:
  artifacts/dish_embeddings.npy
  artifacts/dish_labels.json

Output:
  artifacts/dish_embeddings_v2.npy
  artifacts/dish_labels_v2.json
"""
import json
import numpy as np
from collections import defaultdict
from pathlib import Path

ARTIFACTS = Path("C:/PALM/artifacts")

EMB_IN  = ARTIFACTS / "dish_embeddings.npy"
LAB_IN  = ARTIFACTS / "dish_labels.json"

EMB_OUT = ARTIFACTS / "dish_embeddings_v2.npy"
LAB_OUT = ARTIFACTS / "dish_labels_v2.json"

def main():
    embs = np.load(EMB_IN)
    with open(LAB_IN, "r", encoding="utf-8") as f:
        labels = json.load(f)

    buckets = defaultdict(list)

    for emb, label in zip(embs, labels):
        buckets[label].append(emb)

    new_embs = []
    new_labels = []

    for label, vecs in buckets.items():
        new_embs.append(np.mean(vecs, axis=0))
        new_labels.append(label)

    np.save(EMB_OUT, np.vstack(new_embs))

    with open(LAB_OUT, "w", encoding="utf-8") as f:
        json.dump(new_labels, f, indent=2)

    print(f"[OK] Collapsed {len(labels):,} → {len(new_labels)} dish centroids")
    print(f"[OK] Saved → {EMB_OUT.name}, {LAB_OUT.name}")

if __name__ == "__main__":
    main()

