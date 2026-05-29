from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from food_ai.ui.config import UISettings


@dataclass(frozen=True, slots=True)
class SampleImageCase:
    key: str
    label: str
    path: Path
    category: str
    notes: str
    tags: tuple[str, ...]

    @property
    def display_name(self) -> str:
        return f"{self.label} [{self.category}]"


def _case(
    root: Path,
    *,
    key: str,
    filename: str,
    label: str,
    category: str,
    notes: str,
    tags: tuple[str, ...],
) -> SampleImageCase | None:
    path = root / filename
    if not path.exists():
        return None
    return SampleImageCase(
        key=key,
        label=label,
        path=path,
        category=category,
        notes=notes,
        tags=tags,
    )


def get_sample_image_cases(settings: UISettings | None = None) -> list[SampleImageCase]:
    ui_settings = settings or UISettings()
    root = ui_settings.sample_images_root
    candidates = [
        _case(
            root,
            key="waffles_clean",
            filename="food_1.jpg",
            label="Waffles With Berries",
            category="clear_food",
            notes="Good baseline plated-food case with distinct garnish and syrup.",
            tags=("food detected", "clean baseline"),
        ),
        _case(
            root,
            key="caesar_salad",
            filename="food_4.jpg",
            label="Caesar Salad",
            category="ingredient_heavy",
            notes="Good for checking ingredient plausibility on visible greens, croutons, and cheese.",
            tags=("ingredient focus", "partial hidden ingredients"),
        ),
        _case(
            root,
            key="breakfast_sandwich_close",
            filename="food_5.jpg",
            label="Breakfast Sandwich Close Crop",
            category="difficult_food",
            notes="Tighter crop with shallow depth of field and limited context.",
            tags=("partial food image", "difficult food"),
        ),
        _case(
            root,
            key="salmon_plate",
            filename="food_8.jpg",
            label="Salmon Plate With Sides",
            category="multi_component_food",
            notes="Useful for multi-component plated meal validation.",
            tags=("multiple-food image", "side dishes"),
        ),
        _case(
            root,
            key="meatballs_homemade",
            filename="food_9.jpg",
            label="Meatballs In Sauce",
            category="homemade_food",
            notes="Homemade-style dish that may stress recipe retrieval and ingredient bridging.",
            tags=("uncommon homemade dish", "sauce heavy"),
        ),
        _case(
            root,
            key="package_bar_back",
            filename="IMG_2469.jpeg",
            label="Packaged Bar Back Panel",
            category="packaging_only",
            notes="Packaging-only image with nutrition panel and barcode, not plated food.",
            tags=("packaging-only image", "should fail gracefully"),
        ),
        _case(
            root,
            key="package_box_barcode",
            filename="IMG_2470.jpeg",
            label="Packaged Box Barcode Crop",
            category="packaging_only",
            notes="Another packaging-only case to inspect false-positive food recognition.",
            tags=("packaging-only image", "edge case"),
        ),
        _case(
            root,
            key="empty_table_setting",
            filename="noise_1.jpg",
            label="Empty Table Setting",
            category="non_food",
            notes="Dining scene with no plated food; useful for non-food graceful failure checks.",
            tags=("non-food image test", "false positive risk"),
        ),
        _case(
            root,
            key="texture_noise",
            filename="noise_5.jpg",
            label="Texture Noise",
            category="non_food",
            notes="Pure texture/noise image for hard non-food failure behavior.",
            tags=("non-food image test", "low information"),
        ),
    ]
    return [case for case in candidates if case is not None]


def get_default_sample_case(settings: UISettings | None = None) -> SampleImageCase | None:
    cases = get_sample_image_cases(settings)
    return cases[0] if cases else None


__all__ = ["SampleImageCase", "get_default_sample_case", "get_sample_image_cases"]
