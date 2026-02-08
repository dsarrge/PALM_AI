# -*- coding: utf-8 -*-
"""
Created on Mon Dec 29 17:36:56 2025

@author: dsarr

utils/barcode_read.py
"""

from pathlib import Path

def read_barcode(image_path: str) -> str | None:
    """
    Reads a barcode from an image.
    Returns barcode string or None.
    """
    try:
        import cv2
        from pyzbar.pyzbar import decode
    except ImportError:
        raise RuntimeError("opencv-python and pyzbar are required")

    img_path = Path(image_path)
    if not img_path.exists():
        return None

    img = cv2.imread(str(img_path))
    if img is None:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    codes = decode(gray)
    if not codes:
        return None

    # Prefer EAN / UPC style barcodes
    for c in codes:
        if c.type in {"EAN13", "EAN8", "UPCA", "UPCE"}:
            return c.data.decode("utf-8")

    # Fallback: first detected barcode
    return codes[0].data.decode("utf-8")
