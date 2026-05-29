from __future__ import annotations

from pathlib import Path

import numpy as np


def collapse_dish_embeddings(input_paths: list[str | Path], output_path: str | Path) -> Path:
    arrays = []
    for path in input_paths:
        with np.load(path, allow_pickle=True) as payload:
            arrays.append(payload['embeddings'])
    merged = np.vstack(arrays) if arrays else np.empty((0, 0), dtype=np.float32)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('wb') as handle:
        np.savez_compressed(handle, embeddings=merged)
    return destination
