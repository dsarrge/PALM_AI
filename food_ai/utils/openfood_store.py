from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Any

from food_ai.barcode.service import store_openfood_product


def _normalize_serving_rows(serving_sizes: list[dict[str, Any]]) -> list[tuple[str | None, float, int]]:
    rows: list[tuple[str | None, float, int]] = []
    seen: set[tuple[str | None, float, int]] = set()
    for serving in serving_sizes:
        try:
            grams = float(serving.get("grams", 0.0))
        except (TypeError, ValueError):
            continue
        if grams <= 0.0:
            continue
        raw_name = serving.get("serving_name")
        serving_name = str(raw_name).strip() if raw_name is not None else None
        if serving_name == "":
            serving_name = None
        is_default = int(bool(serving.get("is_default", 0)))
        key = (serving_name, round(grams, 6), is_default)
        if key in seen:
            continue
        seen.add(key)
        rows.append((serving_name, grams, is_default))
    rows.sort(key=lambda row: (-row[2], row[1], row[0] or ""))
    return rows


def store_openfood(db_path: Path, product: dict[str, Any]) -> int:
    product_id = store_openfood_product(product, db_path=db_path)
    serving_rows = _normalize_serving_rows(product.get("serving_sizes") or [])
    if not serving_rows:
        return product_id

    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("DELETE FROM serving_sizes WHERE product_id = ?", (product_id,))
        connection.executemany(
            "INSERT INTO serving_sizes (product_id, serving_name, grams, is_default) VALUES (?, ?, ?, ?)",
            [(product_id, serving_name, grams, is_default) for serving_name, grams, is_default in serving_rows],
        )
        connection.commit()
    return product_id


__all__ = ["store_openfood"]
