from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from fractions import Fraction

from food_ai.food_bridge.ingredient_normalize import normalize_ingredient
from food_ai.food_bridge.usda_portions import normalize_unit


@dataclass
class ParsedIngredientMeasure:
    raw_text: str
    quantity: float | None
    unit: str | None
    ingredient_text: str
    normalized_ingredient: str | None
    parse_confidence: float

    def to_dict(self) -> dict:
        return asdict(self)


UNICODE_FRACTIONS = {
    "\u00bc": "1/4",
    "\u00bd": "1/2",
    "\u00be": "3/4",
    "\u2153": "1/3",
    "\u2154": "2/3",
    "\u215b": "1/8",
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅛": "1/8",
}


def _clean(text: str) -> str:
    value = str(text).strip()
    for char, replacement in UNICODE_FRACTIONS.items():
        value = value.replace(char, f" {replacement} ")
    value = value.replace("(", " ").replace(")", " ")
    return re.sub(r"\s+", " ", value).strip()


def _parse_quantity(text: str) -> tuple[float | None, str]:
    text = text.strip()
    mixed = re.match(r"^(\d+(?:\.\d+)?)\s+(\d+/\d+)\b\s*(.*)$", text)
    if mixed:
        return float(mixed.group(1)) + float(Fraction(mixed.group(2))), mixed.group(3).strip()
    fraction = re.match(r"^(\d+/\d+)\b\s*(.*)$", text)
    if fraction:
        return float(Fraction(fraction.group(1))), fraction.group(2).strip()
    range_match = re.match(r"^(\d+(?:\.\d+)?)\s*(?:-|to)\s*\d+(?:\.\d+)?\b\s*(.*)$", text)
    if range_match:
        return float(range_match.group(1)), range_match.group(2).strip()
    decimal = re.match(r"^(\d+(?:\.\d+)?)\b\s*(.*)$", text)
    if decimal:
        return float(decimal.group(1)), decimal.group(2).strip()
    return None, text


def parse_ingredient_measure(text: str) -> ParsedIngredientMeasure:
    cleaned = _clean(text)
    quantity, remainder = _parse_quantity(cleaned)
    words = remainder.split()
    unit = None
    ingredient_words = words
    if words:
        two_word_unit = " ".join(words[:2])
        normalized_two = normalize_unit(two_word_unit)
        normalized_one = normalize_unit(words[0])
        if normalized_two in {"fluid_ounce"}:
            unit = normalized_two
            ingredient_words = words[2:]
        elif normalized_one in {
            "cup",
            "tablespoon",
            "teaspoon",
            "ounce",
            "pound",
            "gram",
            "kilogram",
            "milliliter",
            "liter",
            "slice",
            "piece",
            "clove",
            "can",
            "stick",
        }:
            unit = normalized_one
            ingredient_words = words[1:]

    ingredient_text = " ".join(ingredient_words).strip(" ,;:")
    normalized_ingredient = normalize_ingredient(ingredient_text)
    confidence = 0.35
    if quantity is not None:
        confidence += 0.35
    if unit is not None:
        confidence += 0.2
    if normalized_ingredient:
        confidence += 0.1
    return ParsedIngredientMeasure(
        raw_text=text,
        quantity=quantity,
        unit=unit,
        ingredient_text=ingredient_text,
        normalized_ingredient=normalized_ingredient,
        parse_confidence=round(min(confidence, 1.0), 3),
    )


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Parse RecipeNLG ingredient measurement lines.")
    parser.add_argument("ingredients", nargs="+")
    args = parser.parse_args()
    for ingredient in args.ingredients:
        print(json.dumps(parse_ingredient_measure(ingredient).to_dict(), indent=2))


if __name__ == "__main__":
    main()
