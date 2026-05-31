from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import tempfile
from typing import Any


@dataclass(frozen=True, slots=True)
class BarcodeScanResult:
    status: str
    barcode: str | None = None
    format: str | None = None
    decoder: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "barcode": self.barcode,
            "format": self.format,
            "decoder": self.decoder,
            "message": self.message,
        }


def _clean_barcode(value: Any) -> str | None:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits
    return text if len(text) >= 8 else None


def _scan_with_pyzbar(image_bytes: bytes) -> BarcodeScanResult | None:
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except Exception:
        return None
    try:
        image = Image.open(BytesIO(image_bytes))
        decoded = decode(image)
    except Exception as exc:
        return BarcodeScanResult(status="decoder_error", decoder="pyzbar", message=str(exc))
    for item in decoded:
        raw = item.data.decode("utf-8", errors="ignore") if getattr(item, "data", None) else ""
        barcode = _clean_barcode(raw)
        if barcode:
            return BarcodeScanResult(
                status="decoded",
                barcode=barcode,
                format=str(getattr(item, "type", None) or "unknown"),
                decoder="pyzbar",
            )
    return None


def _scan_with_opencv(image_bytes: bytes) -> BarcodeScanResult | None:
    try:
        import cv2
    except Exception:
        return None
    if not hasattr(cv2, "barcode_BarcodeDetector"):
        return None
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        image = cv2.imread(str(temp_path))
        if image is None:
            return None
        detector = cv2.barcode_BarcodeDetector()
        result = detector.detectAndDecode(image)
    except Exception as exc:
        return BarcodeScanResult(status="decoder_error", decoder="opencv", message=str(exc))
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    if not isinstance(result, tuple) or not result:
        return None
    decoded_info = result[0]
    decoded_type = result[2] if len(result) > 2 else None
    values = decoded_info if isinstance(decoded_info, (list, tuple)) else [decoded_info]
    types = decoded_type if isinstance(decoded_type, (list, tuple)) else [decoded_type]
    for index, value in enumerate(values):
        barcode = _clean_barcode(value)
        if barcode:
            barcode_format = str(types[index]) if index < len(types) and types[index] else "unknown"
            return BarcodeScanResult(status="decoded", barcode=barcode, format=barcode_format, decoder="opencv")
    return None


def scan_barcode_image(image_bytes: bytes) -> dict[str, Any]:
    if not image_bytes:
        return BarcodeScanResult(status="no_image", message="No image bytes provided.").as_dict()

    decoder_errors: list[str] = []
    for scanner in (_scan_with_pyzbar, _scan_with_opencv):
        result = scanner(image_bytes)
        if result is None:
            continue
        if result.status == "decoded":
            return result.as_dict()
        if result.status == "decoder_error":
            decoder_errors.append(f"{result.decoder}: {result.message}")

    message = "No barcode decoded from image. Crop tightly around the barcode and try a sharper image."
    if decoder_errors:
        message += " Decoder errors: " + "; ".join(decoder_errors)
    else:
        message += " Install pyzbar or opencv-contrib-python for local barcode decoding."
    return BarcodeScanResult(status="not_decoded", message=message).as_dict()


__all__ = ["scan_barcode_image", "BarcodeScanResult"]
