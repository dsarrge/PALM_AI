from __future__ import annotations

from collections import Counter
from typing import Iterable


def classify_core_ingredients(ingredients: Iterable[str]) -> dict[str, int]:
    return dict(Counter(item.strip().lower() for item in ingredients if str(item).strip()))
