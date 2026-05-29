from __future__ import annotations

from dataclasses import dataclass
import re

SEASONING_COMBO = "seasoning_combo"
CHOICE = "choice"
GENERIC_FAT = "generic_fat"
GENERIC_NUT = "generic_nut"
PRODUCT_MIX = "product_mix"
PLAIN = "plain"


@dataclass(frozen=True, slots=True)
class IngredientFragment:
    text: str
    ambiguity_class: str = PLAIN


@dataclass(frozen=True, slots=True)
class ParsedIngredientLine:
    raw_text: str
    fragments: tuple[IngredientFragment, ...]


def _clean_fragment(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"\bto taste\b", " ", lowered)
    lowered = re.sub(r"\bfreshly ground\b", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip(" ,.")
    return lowered


def parse_ingredient_line(text: str) -> ParsedIngredientLine:
    lowered = text.lower().strip()
    if "salt and" in lowered and "pepper" in lowered:
        return ParsedIngredientLine(
            text,
            (
                IngredientFragment("salt", SEASONING_COMBO),
                IngredientFragment("pepper", SEASONING_COMBO),
            ),
        )
    if " or " in lowered:
        left, right = lowered.split(" or ", 1)
        return ParsedIngredientLine(
            text,
            (
                IngredientFragment(_clean_fragment(left), CHOICE),
                IngredientFragment(_clean_fragment(right), CHOICE),
            ),
        )
    cleaned = _clean_fragment(lowered)
    ambiguity = PLAIN
    if re.search(r"\boil\b", cleaned):
        ambiguity = GENERIC_FAT
    elif re.search(r"\bnuts?\b", cleaned):
        ambiguity = GENERIC_NUT
    elif re.search(r"\bcake mix\b", cleaned):
        ambiguity = PRODUCT_MIX
    return ParsedIngredientLine(text, (IngredientFragment(cleaned, ambiguity),))
