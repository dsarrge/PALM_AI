# -*- coding: utf-8 -*-
"""
Created on Mon Dec 29 17:48:19 2025

@author: dsarr
utils/openfood_lookup.py
"""
import requests

URL = "https://world.openfoodfacts.org/api/v2/product/{}.json"

def fetch_product(barcode: str) -> dict | None:
    try:
        r = requests.get(URL.format(barcode), timeout=5)
        r.raise_for_status()
    except requests.exceptions.RequestException:
        # Timeout, offline, DNS failure, rate limit, etc.
        return None

    data = r.json()
    if data.get("status") != 1:
        return None

    p = data["product"]
    nutr = p.get("nutriments", {})

    def g(key):
        try:
            return float(nutr.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "barcode": barcode,
        "product_name": p.get("product_name"),
        "brand": p.get("brands"),
        "quantity": p.get("quantity"),
        "ingredients_text": p.get("ingredients_text"),
        "nutrition": {
            "serving_size_g": g("serving_size"),
            "servings_per_container": g("servings_per_container"),
            "calories": g("energy-kcal"),
            "total_fat_g": g("fat"),
            "saturated_fat_g": g("saturated-fat"),
            "trans_fat_g": g("trans-fat"),
            "cholesterol_mg": g("cholesterol") * 1000,
            "sodium_mg": g("sodium") * 1000,
            "total_carbs_g": g("carbohydrates"),
            "fiber_g": g("fiber"),
            "total_sugars_g": g("sugars"),
            "added_sugars_g": g("added-sugars"),
            "protein_g": g("proteins"),
            "vitamin_d_mcg": g("vitamin-d"),
            "calcium_mg": g("calcium"),
            "iron_mg": g("iron"),
            "potassium_mg": g("potassium"),
            "caffeine_mg": g("caffeine"),
        }
    }
