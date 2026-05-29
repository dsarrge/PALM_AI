from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path
from typing import Any


NUTRITION_KEYS = ("calories", "protein_g", "total_fat_g", "total_carbs_g")


def _sum_with_where(
    connection: sqlite3.Connection,
    select_sql: str,
    where_clause: str,
    params: tuple[object, ...],
) -> float:
    query = f"{select_sql} {where_clause}".strip()
    return float(connection.execute(query, params).fetchone()[0] or 0.0)


def get_daily_totals(db_path: str | Path, date: str | None = None) -> dict[str, Any]:
    with closing(sqlite3.connect(db_path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(consumption_logs)").fetchall()}
        amount_column = "grams_consumed" if "grams_consumed" in columns else "quantity"
        timestamp_column = "consumed_at" if "consumed_at" in columns else "created_at"

        where_clause = ""
        params: tuple[object, ...] = ()
        if date:
            where_clause = f"WHERE date({timestamp_column}) = date(?)"
            params = (date,)

        try:
            total_quantity = _sum_with_where(
                connection,
                f"SELECT COALESCE(SUM({amount_column}), 0) FROM consumption_logs",
                where_clause,
                params,
            )
        except sqlite3.OperationalError:
            total_quantity = 0.0

        nutrition_totals = {key: 0.0 for key in NUTRITION_KEYS}
        if set(NUTRITION_KEYS).issubset(columns):
            for key in NUTRITION_KEYS:
                nutrition_totals[key] = _sum_with_where(
                    connection,
                    f"SELECT COALESCE(SUM({key}), 0) FROM consumption_logs",
                    where_clause,
                    params,
                )
        elif {"source", "ref_id", amount_column}.issubset(columns):
            for key, nf_column in (
                ("calories", "calories"),
                ("protein_g", "protein_g"),
                ("total_fat_g", "total_fat_g"),
                ("total_carbs_g", "total_carbs_g"),
            ):
                nutrition_totals[key] = _sum_with_where(
                    connection,
                    (
                        "SELECT COALESCE(SUM(COALESCE(nf.{nf_column}, 0) * COALESCE(l.{amount_column}, 0) / 100.0), 0) "
                        "FROM consumption_logs l "
                        "LEFT JOIN barcodes b ON b.barcode = l.ref_id "
                        "LEFT JOIN nutrition_facts nf ON nf.product_id = b.product_id "
                        "WHERE l.source = 'barcode'"
                    ).format(nf_column=nf_column, amount_column=amount_column),
                    f"AND date(l.{timestamp_column}) = date(?)" if date else "",
                    params,
                )
        elif {"barcode", amount_column}.issubset(columns):
            for key, nf_column in (
                ("calories", "calories"),
                ("protein_g", "protein_g"),
                ("total_fat_g", "total_fat_g"),
                ("total_carbs_g", "total_carbs_g"),
            ):
                nutrition_totals[key] = _sum_with_where(
                    connection,
                    (
                        "SELECT COALESCE(SUM(COALESCE(nf.{nf_column}, 0) * COALESCE(l.{amount_column}, 0) / 100.0), 0) "
                        "FROM consumption_logs l "
                        "LEFT JOIN barcodes b ON b.barcode = l.barcode "
                        "LEFT JOIN nutrition_facts nf ON nf.product_id = b.product_id"
                    ).format(nf_column=nf_column, amount_column=amount_column),
                    f"WHERE date(l.{timestamp_column}) = date(?)" if date else "",
                    params,
                )

    return {
        "date": date,
        "total_quantity": total_quantity,
        "amount_column": amount_column,
        "nutrition_totals": nutrition_totals,
    }
