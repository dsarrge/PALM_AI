from __future__ import annotations

import re
from typing import Any

from food_ai.utils.ingredient_parser import CHOICE, PRODUCT_MIX, parse_ingredient_line


_UNITS = {
    "tsp", "teaspoon", "teaspoons", "tbsp", "tablespoon", "tablespoons", "c", "cup", "cups",
    "lb", "lbs", "pound", "pounds", "oz", "ounce", "ounces", "stick", "sticks", "box", "slice", "slices",
}

_DIRECT_MAP = {
    "vanilla": "vanilla extract",
    "salt": "salt",
    "baking soda": "baking soda",
    "soda": "baking soda",
    "mayonnaise": "mayonnaise",
    "oleo": "margarine",
    "hamburger": "beef ground",
    "worcestershire sauce": "worcestershire sauce",
    "bay leaf": "bay leaf",
    "cornstarch": "cornstarch",
    "paprika": "paprika",
    "oregano": "oregano",
    "chili powder": "chili powder",
    "cocoa": "cocoa powder",
    "dry mustard": "mustard",
    "mustard": "mustard",
    "vinegar": "vinegar",
    "pepper": "pepper",
    "green pepper": "bell pepper",
    "plain bagle": "bagel",
    "plain bagel": "bagel",
    "bagle": "bagel",
    "bagel": "bagel",
    "wheat bread": "wheat bread",
    "brown sugar": "brown sugar",
}

_PHRASE_ALIASES = {
    "chicken thigh": "chicken thigh",
    "turkey ground": "turkey ground",
    "beef brisket": "beef brisket",
    "pork tenderloin": "pork tenderloin",
    "green pepper": "bell pepper",
    "bell pepper": "bell pepper",
    "walnut": "walnut",
    "pecan": "pecan",
    "tomato": "tomato",
    "pea": "pea",
}


def _strip_measurements(text: str) -> str:
    lowered = text.lower().replace(".", " ")
    lowered = re.sub(r"\([^)]*\)", " ", lowered)
    lowered = re.sub(r"\b\d+[\d/]*\b", " ", lowered)
    lowered = re.sub(r"[^a-z ]+", " ", lowered)
    words = [word for word in lowered.split() if word not in _UNITS]
    return " ".join(words)


def _singularize(text: str) -> str:
    special = {
        "raisins": "raisin",
        "pecans": "pecan",
        "cloves": "clove",
        "walnuts": "walnut",
        "tomatoes": "tomato",
        "peas": "pea",
        "thighs": "thigh",
    }
    if text in special:
        return special[text]
    if text.endswith("ies") and len(text) > 4:
        return text[:-3] + "y"
    if text.endswith("s") and len(text) > 3 and not text.endswith("ss"):
        return text[:-1]
    return text


def _normalize_fragment_text(text: str) -> dict[str, str | None]:
    cleaned = _strip_measurements(text)
    cooked = False
    if cleaned.startswith("cooked "):
        cooked = True
        cleaned = cleaned[len("cooked "):]
    cleaned = re.sub(r"\b(chopped|diced|fresh|plain|large|small|medium)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    metadata: dict[str, str | None] = {
        "raw_ingredient": text,
        "cleaned": cleaned or None,
        "canonical": None,
        "alias_applied": None,
        "normalization_status": "empty_after_clean" if not cleaned else "normalized",
    }
    if not cleaned:
        return metadata

    if cleaned.startswith("ground "):
        cleaned = cleaned[len("ground "):].strip() + " ground"
        metadata["cleaned"] = cleaned

    alias_steps: list[str] = []
    mapped = _DIRECT_MAP.get(cleaned, cleaned)
    if mapped != cleaned:
        alias_steps.append(f"{cleaned}->{mapped}")
    tokens = [_singularize(token) for token in mapped.split()]
    canonical = " ".join(tokens)
    phrase_mapped = _PHRASE_ALIASES.get(canonical, canonical)
    if phrase_mapped != canonical:
        alias_steps.append(f"{canonical}->{phrase_mapped}")
    canonical = phrase_mapped
    if canonical == "butter":
        alias_steps.append("butter->butter unsalted")
        canonical = "butter unsalted"
    if canonical in {"salt pepper", "pepper salt"}:
        metadata["normalization_status"] = "rejected_combined_seasoning"
        return metadata
    if cooked:
        if canonical == "bell pepper":
            canonical = "cooked bell pepper"
        elif canonical == "pea":
            canonical = "cooked pea"
        elif canonical == "tomato":
            canonical = "cooked tomato"

    metadata["canonical"] = canonical or None
    metadata["alias_applied"] = "; ".join(alias_steps) or None
    metadata["normalization_status"] = "normalized" if canonical else "empty_after_normalization"
    return metadata


def normalize_ingredient_with_metadata(text: str) -> dict[str, Any]:
    parsed = parse_ingredient_line(text)
    metadata: dict[str, Any] = {
        "raw_ingredient": text,
        "cleaned": None,
        "canonical": None,
        "alias_applied": None,
        "normalization_status": "unparsed",
        "ambiguity_class": parsed.fragments[0].ambiguity_class if parsed.fragments else None,
        "components": [],
    }
    if not parsed.fragments:
        metadata["normalization_status"] = "empty"
        return metadata

    if len(parsed.fragments) > 1:
        components: list[dict[str, Any]] = []
        for fragment in parsed.fragments:
            if fragment.ambiguity_class in {CHOICE, PRODUCT_MIX}:
                components.append({
                    "raw_ingredient": fragment.text,
                    "cleaned": None,
                    "canonical": None,
                    "alias_applied": None,
                    "normalization_status": "ambiguous_choice",
                    "ambiguity_class": fragment.ambiguity_class,
                })
                continue
            component = _normalize_fragment_text(fragment.text)
            component["ambiguity_class"] = fragment.ambiguity_class
            components.append(component)

        metadata["components"] = components
        cleaned_values = [str(item["cleaned"]) for item in components if item.get("cleaned")]
        canonical_values = [str(item["canonical"]) for item in components if item.get("canonical")]
        alias_values = [str(item["alias_applied"]) for item in components if item.get("alias_applied")]
        metadata["cleaned"] = " | ".join(cleaned_values) or None
        metadata["canonical"] = "; ".join(canonical_values) or None
        metadata["alias_applied"] = "; ".join(alias_values) or None
        if canonical_values and len(canonical_values) == len(components):
            metadata["normalization_status"] = "normalized_multi_fragment"
        elif canonical_values:
            metadata["normalization_status"] = "partially_normalized_multi_fragment"
        else:
            metadata["normalization_status"] = "ambiguous_multi_fragment"
        return metadata

    fragment = parsed.fragments[0]
    if fragment.ambiguity_class in {CHOICE, PRODUCT_MIX}:
        metadata["normalization_status"] = "ambiguous_choice"
        return metadata

    fragment_metadata = _normalize_fragment_text(fragment.text)
    metadata.update(fragment_metadata)
    return metadata


def normalize_ingredient_text(text: str) -> str | None:
    metadata = normalize_ingredient_with_metadata(text)
    if metadata.get("normalization_status") != "normalized":
        return None
    return metadata.get("canonical")


def normalize_ingredient_line(text: str, include_choices: bool = False) -> list[str]:
    parsed = parse_ingredient_line(text)
    results: list[str] = []
    for fragment in parsed.fragments:
        if fragment.ambiguity_class == CHOICE:
            if not include_choices:
                continue
            choice_clean = _strip_measurements(fragment.text)
            choice_clean = re.sub(r"\s+", " ", choice_clean).strip()
            if choice_clean == "butter":
                results.append("butter unsalted")
            elif choice_clean == "margarine":
                results.append("margarine")
            else:
                normalized = normalize_ingredient_text(choice_clean)
                if normalized is not None:
                    results.append(normalized)
            continue
        normalized = normalize_ingredient_text(fragment.text)
        if normalized is not None:
            results.append(normalized)
    return results
