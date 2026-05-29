from __future__ import annotations

import re

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



def normalize_ingredient_with_metadata(text: str) -> dict[str, str | None]:
    parsed = parse_ingredient_line(text)
    metadata: dict[str, str | None] = {
        "raw_ingredient": text,
        "cleaned": None,
        "canonical": None,
        "alias_applied": None,
        "normalization_status": "unparsed",
        "ambiguity_class": parsed.fragments[0].ambiguity_class if parsed.fragments else None,
    }
    if not parsed.fragments:
        metadata["normalization_status"] = "empty"
        return metadata
    if len(parsed.fragments) > 1:
        metadata["normalization_status"] = "ambiguous_multi_fragment"
        return metadata

    fragment = parsed.fragments[0]
    if fragment.ambiguity_class in {CHOICE, PRODUCT_MIX}:
        metadata["normalization_status"] = "ambiguous_choice"
        return metadata

    cleaned = _strip_measurements(fragment.text)
    cooked = False
    if cleaned.startswith("cooked "):
        cooked = True
        cleaned = cleaned[len("cooked "):]
    cleaned = re.sub(r"\b(chopped|diced|fresh|plain|large|small|medium)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    metadata["cleaned"] = cleaned or None
    if not cleaned:
        metadata["normalization_status"] = "empty_after_clean"
        return metadata

    if cleaned.startswith("ground "):
        cleaned = cleaned[len("ground "):].strip() + " ground"

    alias_applied = None
    mapped = _DIRECT_MAP.get(cleaned, cleaned)
    if mapped != cleaned:
        alias_applied = f"{cleaned}->{mapped}"
    tokens = [_singularize(token) for token in mapped.split()]
    canonical = " ".join(tokens)
    phrase_mapped = _PHRASE_ALIASES.get(canonical, canonical)
    if phrase_mapped != canonical:
        alias_applied = f"{canonical}->{phrase_mapped}"
    canonical = phrase_mapped
    if canonical == "butter":
        alias_applied = alias_applied or "butter->butter unsalted"
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
    metadata["alias_applied"] = alias_applied
    metadata["normalization_status"] = "normalized" if canonical else "empty_after_normalization"
    return metadata



def normalize_ingredient_text(text: str) -> str | None:
    metadata = normalize_ingredient_with_metadata(text)
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
