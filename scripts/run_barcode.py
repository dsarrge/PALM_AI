# -*- coding: utf-8 -*-
"""
Created on Sat Feb  7 17:46:19 2026

@author: dsarr

food_ai/scripts/run_barcode.py
"""

from food_ai.core.barcode import read_barcode
from food_ai.core.openfood import fetch_product
from food_ai.data.store import store_openfood

DB_PATH = "C:\PALM\shared\palm.db"

def main(image_path: str):
    barcode = read_barcode(image_path)
    if not barcode:
        print("No barcode detected")
        return

    product = fetch_product(barcode)
    if not product:
        print("Product not found in Open Food Facts")
        return

    store_openfood(DB_PATH, product)
    print(f"Stored product: {product['product_name']}")

if __name__ == "__main__":
    import sys
    main(sys.argv[1])
