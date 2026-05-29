from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from food_ai.barcode.service import lookup_local_barcode
from food_ai.config import DB_PATH
from food_ai.core.daily_totals import get_daily_totals
from food_ai.core.log_consumption import log_consumption
from food_ai.utils.barcode_read import read_barcode
from food_ai.utils.openfood_lookup import fetch_product
from food_ai.utils.openfood_store import store_openfood


def _resolve_barcode(image_path: str | None, barcode: str | None) -> str | None:
    if barcode:
        return str(barcode).strip()
    if not image_path:
        return None
    return read_barcode(image_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Barcode pipeline: read -> lookup/fetch -> store -> optional log -> totals")
    parser.add_argument("image_path", nargs="?", default=None, help="Path to barcode image. Optional if --barcode is provided.")
    parser.add_argument("--barcode", dest="barcode", default=None, help="Direct barcode input.")
    parser.add_argument("--db", dest="db_path", type=Path, default=DB_PATH)
    parser.add_argument("--offline", action="store_true", help="Skip online OpenFoodFacts fetch.")
    parser.add_argument("--quantity", type=float, default=0.0, help="Grams to log after lookup/store. 0 disables logging.")
    parser.add_argument("--date", default=None, help="Optional YYYY-MM-DD date filter for totals output.")
    args = parser.parse_args()

    barcode = _resolve_barcode(args.image_path, args.barcode)
    if not barcode:
        raise SystemExit("No barcode detected or provided.")

    product = lookup_local_barcode(barcode, args.db_path)
    fetched = None
    if product is None and not args.offline:
        fetched = fetch_product(barcode)
        if fetched is not None:
            store_openfood(args.db_path, fetched)
            product = lookup_local_barcode(barcode, args.db_path)

    if product is None:
        raise SystemExit(f"Barcode not found locally and fetch failed: {barcode}")

    payload: dict[str, object] = {
        "barcode": barcode,
        "product": product,
        "fetched_and_stored": bool(fetched is not None),
    }

    if args.quantity > 0.0:
        payload["log_result"] = log_consumption(barcode, args.quantity, args.db_path)

    payload["daily_totals"] = get_daily_totals(args.db_path, date=args.date)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
