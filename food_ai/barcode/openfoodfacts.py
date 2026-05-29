from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
import re
from typing import Any

import requests


@dataclass(frozen=True, slots=True)
class ServingSize:
    serving_name: str
    grams: float
    is_default: int = 0


OPENFOODFACTS_URL_TEMPLATE = "https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
OPENFOODFACTS_TIMEOUT_SECONDS = 8.0


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _extract_serving_sizes(text: str | None) -> list[ServingSize]:
    if not text:
        return []
    match = re.search(r"\((?P<grams>\d+(?:\.\d+)?)\s*g\)", text.lower())
    if not match:
        return []
    grams = float(match.group("grams"))
    return [ServingSize(serving_name=str(text), grams=grams, is_default=1)]


def _infer_barcode_format(barcode: str) -> str:
    digits = re.sub(r"\D+", "", str(barcode))
    if len(digits) == 8:
        return "ean8"
    if len(digits) == 12:
        return "upca"
    if len(digits) == 13:
        return "ean13"
    return "unknown"


def _extract_nutrition(nutriments: dict[str, Any]) -> dict[str, float]:
    calories = _safe_float(
        nutriments.get("energy-kcal_100g")
        or nutriments.get("energy-kcal")
        or nutriments.get("energy_100g")
    )
    protein_g = _safe_float(nutriments.get("proteins_100g") or nutriments.get("proteins"))
    total_fat_g = _safe_float(nutriments.get("fat_100g") or nutriments.get("fat"))
    total_carbs_g = _safe_float(nutriments.get("carbohydrates_100g") or nutriments.get("carbohydrates"))
    return {
        "calories": calories or 0.0,
        "protein_g": protein_g or 0.0,
        "total_fat_g": total_fat_g or 0.0,
        "total_carbs_g": total_carbs_g or 0.0,
    }


def fetch_openfood_product(barcode: str, timeout_seconds: float = OPENFOODFACTS_TIMEOUT_SECONDS) -> dict[str, Any] | None:
    cleaned_barcode = re.sub(r"\D+", "", str(barcode))
    if not cleaned_barcode:
        return None

    try:
        response = requests.get(
            OPENFOODFACTS_URL_TEMPLATE.format(barcode=cleaned_barcode),
            timeout=timeout_seconds,
            headers={"User-Agent": "PALM-Food-AI/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    if int(payload.get("status") or 0) != 1:
        return None

    product_data = payload.get("product") or {}
    serving_text = str(product_data.get("serving_size") or "").strip() or None
    servings = _extract_serving_sizes(serving_text)
    if not servings:
        serving_quantity = _safe_float(product_data.get("serving_quantity"))
        if serving_quantity and serving_quantity > 0.0:
            servings = [ServingSize(serving_name="default serving", grams=serving_quantity, is_default=1)]

    nutrition = _extract_nutrition(product_data.get("nutriments") or {})
    product_name = str(product_data.get("product_name") or "").strip()
    if not product_name:
        return None

    brand = str(product_data.get("brands") or "").split(",")[0].strip() or None
    category = str(product_data.get("categories") or "").strip() or None
    return {
        "barcode": cleaned_barcode,
        "product_name": product_name,
        "brand": brand,
        "category": category,
        "source": "openfoodfacts",
        "verified": 0,
        "format": _infer_barcode_format(cleaned_barcode),
        "serving_sizes": [asdict(item) for item in servings],
        "nutrition": nutrition,
    }


__all__ = [
    "ServingSize",
    "_extract_serving_sizes",
    "fetch_openfood_product",
]
