from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any


def load_embedding_cache(path: str | Path) -> dict[str, Any]:
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    with cache_path.open('rb') as handle:
        return pickle.load(handle)


def save_embedding_cache(path: str | Path, payload: dict[str, Any]) -> Path:
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open('wb') as handle:
        pickle.dump(payload, handle)
    return cache_path
