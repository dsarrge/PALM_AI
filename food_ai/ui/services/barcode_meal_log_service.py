from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from food_ai.barcode.service import lookup_local_barcode, record_unknown_barcode
from food_ai.config import resolve_runtime_db_path
from food_ai.ui.services.meal_log_service import ensure_meal_log_schema


NUTRIENT_KEYS = (
    "calories",
    "protein_g",
    "total_fat_g",
    "total_carbs_g",
    "fiber_g",
    "total_sugars_g",
    "added_sugars_g",
    "sodium_mg",
    "cholesterol_mg",
)


def _as_float(value: Any) -> float | None:
    if value in (None, "", "n/a"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _nutrition_from_product(product: dict[str, Any]) -> dict[str, float | None]:
    nutrition = dict(product.get("nutrition") or {})
    out: dict[str, float | None] = {}
    aliases = {
        "total_fat_g": ("total_fat_g", "fat_g"),
        "total_carbs_g": ("total_carbs_g", "carbs_g"),
    }
    for key in NUTRIENT_KEYS:
        value = None
        for candidate in aliases.get(key, (key,)):
            value = _as_float(nutrition.get(candidate) or product.get(candidate))
            if value is not None:
                break
        out[key] = value
    return out


def _scale(per_100g: dict[str, float | None], grams: float) -> dict[str, float | None]:
    multiplier = max(float(grams), 0.0) / 100.0
    return {key: round(value * multiplier, 3) if value is not None else None for key, value in per_100g.items()}


def lookup_barcode_product(barcode: str, db_path: str | Path | None = None) -> dict[str, Any]:
    clean_barcode = "".join(ch for ch in str(barcode or "") if ch.isdigit())
    if not clean_barcode:
        return {"status": "missing_barcode"}
    resolved_db = resolve_runtime_db_path(db_path)
    product = lookup_local_barcode(clean_barcode, resolved_db)
    if product is None:
        with sqlite3.connect(resolved_db, timeout=30) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS unknown_barcodes (barcode TEXT PRIMARY KEY, first_seen TEXT DEFAULT CURRENT_TIMESTAMP)")
            connection.commit()
        record_unknown_barcode(clean_barcode, resolved_db)
        return {"status": "unknown_barcode", "barcode": clean_barcode}
    return {"status": "found", "barcode": clean_barcode, "product": product}


def save_barcode_meal_log(
    *,
    barcode: str,
    grams_consumed: float,
    db_path: str | Path | None = None,
    notes: str = "",
) -> dict[str, Any]:
    grams = float(grams_consumed)
    if grams <= 0.0:
        raise ValueError("grams_consumed must be > 0")
    lookup = lookup_barcode_product(barcode, db_path=db_path)
    if lookup.get("status") != "found":
        return lookup

    resolved_db = resolve_runtime_db_path(db_path)
    product = dict(lookup["product"])
    meal_log_id = uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    per_100g = _nutrition_from_product(product)
    total = _scale(per_100g, grams)
    product_name = str(product.get("product_name") or "Packaged food").strip() or "Packaged food"
    brand = str(product.get("brand") or "").strip()
    dish_name = f"{brand} {product_name}".strip()

    ingredient_row = {
        "ingredient": dish_name,
        "matched_ingredient": product_name,
        "include_in_nutrition": True,
        "included_in_nutrition": True,
        "grams_per_100g_dish": 100.0,
        "grams_default": 100.0,
        "weight_source": "barcode_product",
        "portion_method": "packaged_food_barcode",
        "matched_fdc_id": product.get("source_fdc_id"),
        "fdc_id": product.get("source_fdc_id"),
        "calories_contrib": per_100g.get("calories"),
        "protein_g_contrib": per_100g.get("protein_g"),
        "total_fat_g_contrib": per_100g.get("total_fat_g"),
        "total_carbs_g_contrib": per_100g.get("total_carbs_g"),
        "fiber_g_contrib": per_100g.get("fiber_g"),
        "total_sugars_g_contrib": per_100g.get("total_sugars_g"),
        "added_sugars_g_contrib": per_100g.get("added_sugars_g"),
        "sodium_mg_contrib": per_100g.get("sodium_mg"),
        "cholesterol_mg_contrib": per_100g.get("cholesterol_mg"),
    }

    with sqlite3.connect(resolved_db, timeout=30) as connection:
        connection.execute("PRAGMA busy_timeout = 30000")
        ensure_meal_log_schema(connection)
        connection.execute(
            """
            INSERT INTO meal_logs (
                meal_log_id, created_at, eaten_at, image_path, dish_name, user_label,
                confidence, confidence_label, serving_weight_g,
                calories, protein_g, total_fat_g, total_carbs_g, fiber_g,
                total_sugars_g, added_sugars_g, sodium_mg, cholesterol_mg,
                nutrition_per_100g_json, nutrition_total_json,
                included_ingredients_json, excluded_ingredients_json,
                notes, inference_summary_json, inference_result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meal_log_id,
                created_at,
                created_at,
                None,
                dish_name,
                dish_name,
                1.0,
                "100.0%",
                grams,
                total.get("calories"),
                total.get("protein_g"),
                total.get("total_fat_g"),
                total.get("total_carbs_g"),
                total.get("fiber_g"),
                total.get("total_sugars_g"),
                total.get("added_sugars_g"),
                total.get("sodium_mg"),
                total.get("cholesterol_mg"),
                _json_dumps(per_100g),
                _json_dumps(total),
                _json_dumps([ingredient_row]),
                _json_dumps([]),
                notes.strip() or None,
                _json_dumps({"top_dish": dish_name, "source": "barcode", "barcode": lookup["barcode"]}),
                _json_dumps({"source": "barcode", "barcode": lookup["barcode"], "product": product}),
            ),
        )
        cursor = connection.execute(
            """
            INSERT INTO consumption_logs (
                source, ref_id, product_id, grams_consumed, consumed_at,
                calories, protein_g, total_fat_g, total_carbs_g, fiber_g,
                total_sugars_g, added_sugars_g, sodium_mg, cholesterol_mg,
                confidence, flags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "barcode",
                lookup["barcode"],
                product.get("product_id"),
                grams,
                created_at,
                total.get("calories"),
                total.get("protein_g"),
                total.get("total_fat_g"),
                total.get("total_carbs_g"),
                total.get("fiber_g"),
                total.get("total_sugars_g"),
                total.get("added_sugars_g"),
                total.get("sodium_mg"),
                total.get("cholesterol_mg"),
                1.0,
                None,
            ),
        )
        log_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO meal_log_ingredients (
                log_id, canonical_food_id, ingredient_name, matched_ingredient,
                included_in_nutrition, grams_in_meal, grams_per_100g_dish,
                calories, protein_g, total_fat_g, total_carbs_g, fiber_g,
                total_sugars_g, added_sugars_g, sodium_mg, cholesterol_mg, source_fdc_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                None,
                dish_name,
                product_name,
                1,
                grams,
                100.0,
                total.get("calories"),
                total.get("protein_g"),
                total.get("total_fat_g"),
                total.get("total_carbs_g"),
                total.get("fiber_g"),
                total.get("total_sugars_g"),
                total.get("added_sugars_g"),
                total.get("sodium_mg"),
                total.get("cholesterol_mg"),
                product.get("source_fdc_id"),
            ),
        )
        connection.commit()

    return {
        "status": "saved",
        "meal_log_id": meal_log_id,
        "barcode": lookup["barcode"],
        "product": product,
        "grams_consumed": grams,
        "nutrition_total": total,
    }


__all__ = ["lookup_barcode_product", "save_barcode_meal_log"]
