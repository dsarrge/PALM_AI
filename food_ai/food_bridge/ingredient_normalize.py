from __future__ import annotations

import re


UNITS = {
    "c",
    "can",
    "cans",
    "clove",
    "cloves",
    "cup",
    "cups",
    "g",
    "gram",
    "grams",
    "kg",
    "lb",
    "lbs",
    "ounce",
    "ounces",
    "oz",
    "package",
    "packages",
    "pinch",
    "pound",
    "pounds",
    "slice",
    "slices",
    "tablespoon",
    "tablespoons",
    "tbsp",
    "teaspoon",
    "teaspoons",
    "tsp",
}

PREP_WORDS = {
    "boneless",
    "chopped",
    "cooked",
    "cored",
    "crushed",
    "diced",
    "drained",
    "dry",
    "fresh",
    "freshly",
    "frozen",
    "grated",
    "large",
    "medium",
    "minced",
    "peeled",
    "plain",
    "raw",
    "shredded",
    "skinless",
    "sliced",
    "small",
    "thinly",
    "whole",
}

CANONICAL_INGREDIENT_ALIASES = {
    "all purpose flour": "wheat flour",
    "all-purpose flour": "wheat flour",
    "beef mince": "ground beef",
    "canola oil": "oil",
    "cheddar cheese": "cheddar",
    "cheese blend": "cheese",
    "confectioner sugar": "powdered sugar",
    "confectioners sugar": "powdered sugar",
    "cottage cheese": "cottage cheese",
    "cream cheese": "cream cheese",
    "elbow macaroni": "macaroni",
    "egg": "egg",
    "fettuccine noodle": "fettuccine",
    "fresh basil": "basil",
    "glaze": "sugar",
    "flour": "wheat flour",
    "golden delicious apple": "apple",
    "granulated sugar": "sugar",
    "granny smith apple": "apple",
    "green pepper": "bell pepper",
    "italian cheese blend": "cheese",
    "italian sausage": "sausage",
    "lasagna": "lasagna noodle",
    "lasagne": "lasagna noodle",
    "lasagne noodle": "lasagna noodle",
    "lasagne noodles": "lasagna noodle",
    "lasagne sheet": "lasagna noodle",
    "lasagne sheets": "lasagna noodle",
    "lasagna noodles": "lasagna noodle",
    "lasagna sheet": "lasagna noodle",
    "lasagna sheets": "lasagna noodle",
    "macaroni noodle": "macaroni",
    "marinara sauce": "tomato sauce",
    "mozzarella cheese": "mozzarella",
    "noodle": "pasta",
    "parmesan cheese": "parmesan",
    "pasta noodle": "pasta",
    "pasta sauce": "tomato sauce",
    "pecorino romano": "romano cheese",
    "penne pasta": "penne",
    "pork sausage": "sausage",
    "powdered sugar": "powdered sugar",
    "provolone cheese": "provolone",
    "ricotta cheese": "ricotta",
    "rigatoni pasta": "rigatoni",
    "romano cheese": "romano cheese",
    "salted butter": "butter",
    "spaghetti sauce": "tomato sauce",
    "spaghetti noodle": "spaghetti",
    "spaghetti pasta": "spaghetti",
    "swiss cheese": "swiss cheese",
    "unsalted butter": "butter",
    "tomato sauce": "tomato sauce",
    "olive oil": "olive oil",
    "vegetable oil": "oil",
    "vanilla extract": "vanilla extract",
    "whole milk": "milk",
    "chicken breast": "chicken breast",
    "ground beef": "ground beef",
    "baking apple": "apple",
    "cooking apple": "apple",
    "mixed apple": "apple",
    "tart apple": "apple",
}

ALIASES = CANONICAL_INGREDIENT_ALIASES


def ingredient_aliases_for(canonical: str) -> list[str]:
    normalized = str(canonical).strip().lower()
    aliases = [
        alias
        for alias, target in CANONICAL_INGREDIENT_ALIASES.items()
        if target == normalized and alias != normalized
    ]
    return sorted(set(aliases))


def _singularize_word(word: str) -> str:
    special = {
        "apples": "apple",
        "berries": "berry",
        "cheese": "cheese",
        "cloves": "clove",
        "fries": "fry",
        "leaves": "leaf",
        "loaves": "loaf",
        "sauce": "sauce",
        "potatoes": "potato",
        "tomatoes": "tomato",
    }
    if word in special:
        return special[word]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "ses", "xes", "zes")) and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and len(word) > 3 and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize_ingredient(text: str) -> str | None:
    lowered = str(text).lower().strip()
    lowered = re.sub(r"\([^)]*\)", " ", lowered)
    lowered = re.sub(r"\b\d+[\d/.\-]*\b", " ", lowered)
    lowered = re.sub(r"[^a-z ]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    if lowered in {"minced beef", "beef mince"}:
        return "ground beef"
    words = [word for word in lowered.split() if word not in UNITS and word not in PREP_WORDS]
    if not words:
        return None
    normalized = " ".join(_singularize_word(word) for word in words)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"\s+(and|or)$", "", normalized).strip()
    if normalized.startswith("ground "):
        parts = normalized.split()
        if len(parts) > 1:
            normalized = " ".join(parts[1:] + ["ground"])
    normalized = CANONICAL_INGREDIENT_ALIASES.get(normalized, normalized)
    if normalized == "beef ground":
        normalized = "ground beef"
    return normalized or None
