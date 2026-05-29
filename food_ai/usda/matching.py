from __future__ import annotations

import re

from food_ai.utils.usda_normalize import normalize_usda_ingredient

_STOPWORDS = {
    "raw", "cooked", "roasted", "boiled", "drained", "without", "with", "only", "lean", "fat",
    "fresh", "fluid", "prepared", "regular", "mixed", "species", "meat", "skin", "boneless",
    "domesticated", "whole", "home", "prepared", "dry", "heat", "cultured", "plain", "enriched",
}


def _tokens(text: str) -> set[str]:
    lowered = re.sub(r"[^a-z ]+", " ", text.lower())
    return {token for token in lowered.split() if token and token not in _STOPWORDS}


def lexical_match_score(query: str, description: str) -> float:
    query_norm = (query or "").lower().strip()
    normalized_candidate = normalize_usda_ingredient(description)
    candidate_source = normalized_candidate or description.lower().strip()

    query_tokens = _tokens(query_norm)
    candidate_tokens = _tokens(candidate_source)
    overlap = len(query_tokens & candidate_tokens)
    extras = len(candidate_tokens - query_tokens)
    missing = len(query_tokens - candidate_tokens)
    score = 2.4 * overlap - 0.45 * extras - 0.35 * missing

    animals = {"beef", "pork", "chicken", "duck", "turkey", "quail", "snapper", "fish"}
    query_animals = query_tokens & animals
    candidate_animals = candidate_tokens & animals
    if query_animals and candidate_animals and query_animals != candidate_animals:
        score -= 3.5
    elif query_animals and not candidate_animals:
        score -= 2.0

    specificity = {"loin", "leg", "wing", "flank", "tenderloin", "ground", "liver", "giblet", "broth", "stock", "milk", "juice"}
    for token in specificity:
        if token in query_tokens and token not in candidate_tokens:
            score -= 1.7
    if "liver" in candidate_tokens and "liver" not in query_tokens:
        score -= 1.6
    if "giblet" in candidate_tokens and "giblet" not in query_tokens:
        score -= 1.6
    if "broth" in query_tokens and not ({"broth", "stock"} & candidate_tokens):
        score -= 2.2
    if "broth" in query_tokens and "no broth" in description.lower():
        score -= 4.5
    if "milk" in query_tokens and "milk" not in candidate_tokens:
        score -= 2.2
    if query_norm == "bread" and any(token in candidate_source for token in ("cinnamon", "potato")):
        score -= 1.2
    if query_norm == "duck" and "liver" in candidate_tokens:
        score -= 1.2
    if query_norm == "beef" and "ground" in candidate_tokens and "composite of trimmed retail cuts" not in description.lower():
        score -= 1.6
    if query_norm == "beef" and "grass fed" in description.lower():
        score -= 0.8
    if query_norm == "beef" and "composite of trimmed retail cuts" in description.lower():
        score += 2.6
    if query_norm == "almond milk" and "sheep milk" in candidate_source:
        score -= 3.0
    return score
