from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from pathlib import Path
from typing import Any

from food_ai.barcode.service import lookup_local_barcode, record_unknown_barcode


def _consumption_log_columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(consumption_logs)").fetchall()}


def _ensure_consumption_logs_table(connection: sqlite3.Connection) -> set[str]:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS consumption_logs (log_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, ref_id TEXT, grams_consumed REAL NOT NULL, consumed_at TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    return _consumption_log_columns(connection)


def _macro_snapshot_for_quantity(product: dict[str, Any], quantity_grams: float) -> dict[str, float]:
    nutrition = product.get("nutrition") or {}
    scale = float(quantity_grams) / 100.0
    return {
        "calories": float(nutrition.get("calories") or 0.0) * scale,
        "protein_g": float(nutrition.get("protein_g") or 0.0) * scale,
        "total_fat_g": float(nutrition.get("total_fat_g") or 0.0) * scale,
        "total_carbs_g": float(nutrition.get("total_carbs_g") or 0.0) * scale,
    }


def _insert_with_available_columns(connection: sqlite3.Connection, columns: set[str], payload: dict[str, Any]) -> None:
    ordered = [key for key in payload if key in columns]
    if not ordered:
        raise sqlite3.OperationalError("No compatible consumption_logs columns for insert payload")
    placeholders = ", ".join(["?"] * len(ordered))
    sql = f"INSERT INTO consumption_logs ({', '.join(ordered)}) VALUES ({placeholders})"
    values = tuple(payload[key] for key in ordered)
    connection.execute(sql, values)


def log_consumption(barcode: str, quantity: float, db_path: str | Path) -> dict[str, Any]:
    quantity_value = float(quantity)
    if quantity_value <= 0.0:
        raise ValueError("quantity must be > 0")

    product = lookup_local_barcode(barcode, db_path)
    if product is None:
        try:
            record_unknown_barcode(barcode, db_path)
        except sqlite3.OperationalError as exc:
            if "unknown_barcodes" not in str(exc).lower():
                raise
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS unknown_barcodes (barcode TEXT PRIMARY KEY, first_seen TEXT DEFAULT CURRENT_TIMESTAMP)"
                )
                connection.execute("INSERT OR IGNORE INTO unknown_barcodes (barcode) VALUES (?)", (barcode,))
                connection.commit()
        return {"status": "unknown_barcode", "barcode": barcode, "quantity": quantity}

    consumed_at = datetime.now(timezone.utc).isoformat()
    nutrition_snapshot = _macro_snapshot_for_quantity(product, quantity_value)

    with closing(sqlite3.connect(db_path)) as connection:
        columns = _ensure_consumption_logs_table(connection)
        if {"source", "ref_id", "grams_consumed", "consumed_at"}.issubset(columns):
            payload: dict[str, Any] = {
                "source": "barcode",
                "ref_id": barcode,
                "grams_consumed": quantity_value,
                "consumed_at": consumed_at,
            }
            if "confidence" in columns:
                payload["confidence"] = 1.0
            if "flags" in columns:
                payload["flags"] = None
            payload.update(nutrition_snapshot)
            _insert_with_available_columns(connection, columns, payload)
            mode = "source_ref_id"
        elif {"barcode", "grams_consumed"}.issubset(columns):
            payload = {
                "barcode": barcode,
                "grams_consumed": quantity_value,
                "consumed_at": consumed_at,
            }
            payload.update(nutrition_snapshot)
            _insert_with_available_columns(connection, columns, payload)
            mode = "barcode_grams"
        elif {"barcode", "quantity"}.issubset(columns):
            payload = {
                "barcode": barcode,
                "quantity": quantity_value,
                "consumed_at": consumed_at,
            }
            payload.update(nutrition_snapshot)
            _insert_with_available_columns(connection, columns, payload)
            mode = "barcode_quantity"
        else:
            raise sqlite3.OperationalError(f"Unsupported consumption_logs schema columns: {sorted(columns)}")
        connection.commit()

    return {
        "status": "logged",
        "barcode": barcode,
        "quantity": quantity,
        "product": product,
        "log_mode": mode,
        "consumed_at": consumed_at,
        "nutrition_snapshot": nutrition_snapshot,
    }
