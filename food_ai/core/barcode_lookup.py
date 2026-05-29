from food_ai.barcode.service import lookup_barcode, lookup_local_barcode, record_unknown_barcode

lookup_local = lookup_local_barcode
record_unknown = record_unknown_barcode

__all__ = [
    "lookup_barcode",
    "lookup_local_barcode",
    "lookup_local",
    "record_unknown_barcode",
    "record_unknown",
]
