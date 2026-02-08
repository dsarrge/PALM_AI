# -*- coding: utf-8 -*-
"""
Created on Mon Dec 29 11:22:36 2025

@author: dsarr
"""
import torch
import torch.nn as nn
import numpy as np
import json
from tqdm import tqdm
from torchvision import models, transforms
from datasets import load_dataset
from PIL import Image

import os

# CONFIG
DEVICE  = "cuda" #if torch.cuda.is_available() else "cpu"
OUT_DIR = "artifacts"
os.makedirs(OUT_DIR, exist_ok=True)
MAX_SAMPLES  = None        # set int to cap for testing
BATCH_SIZE   = 32

# TRANSFORMS
transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225]
    )
])

# MODEL
base = models.efficientnet_b0(weights="IMAGENET1K_V1")
embedding_dim = base.classifier[1].in_features
base.classifier = nn.Identity()
model = base.to(DEVICE)
model.eval()

# STREAM DATASET
dataset = load_dataset(
    "food101",
    split="train",
    streaming=True
)

# HELPERS
def batchify(iterable, size):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch

# EMBEDDING LOOP
embeddings = []
labels     = []

seen = 0
progress = tqdm(total=MAX_SAMPLES)

with torch.no_grad():
    for batch in batchify(dataset, BATCH_SIZE):
        imgs = []
        batch_labels = []

        for sample in batch:
            img = sample["image"]
            if not isinstance(img, Image.Image):
                continue
        
            img = img.convert("RGB")  # 🔑 FIX
            imgs.append(transform(img))
            batch_labels.append(sample["label"])

            seen += 1
            if MAX_SAMPLES and seen >= MAX_SAMPLES:
                break

        if not imgs:
            continue

        x = torch.stack(imgs).to(DEVICE)
        emb = model(x).cpu().numpy()

        embeddings.append(emb)
        labels.extend(batch_labels)

        progress.update(len(imgs))

        if MAX_SAMPLES and seen >= MAX_SAMPLES:
            break

progress.close()

# FINALIZE
embeddings = np.vstack(embeddings)

# Convert numeric labels → class names
features = load_dataset("food101", split="train", streaming=True).features
id2label = features["label"].names
labels   = [id2label[i] for i in labels]

# SAVE ARTIFACTS
np.save(f"{OUT_DIR}/dish_embeddings.npy", embeddings)

with open(f"{OUT_DIR}/dish_labels.json", "w") as f:
    json.dump(labels, f)

torch.save(model.state_dict(), f"{OUT_DIR}/food_embedder.pt")

# MANIFEST (IMPORTANT)
manifest = {
    "dataset": "food101",
    "split": "train",
    "streaming": True,
    "model": "efficientnet_b0",
    "embedding_dim": embedding_dim,
    "samples_used": len(labels)
}

with open(f"{OUT_DIR}/data_manifest.json", "w") as f:
    json.dump(manifest, f, indent=2)

print("✅ Streaming embedding export complete")
