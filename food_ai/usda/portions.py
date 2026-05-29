from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PortionEstimate:
    description: str
    grams: float


def normalize_portion(description: str, grams: float) -> PortionEstimate:
    return PortionEstimate(description=description.strip().lower(), grams=float(grams))
