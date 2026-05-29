from __future__ import annotations

import pandas as pd

from food_ai.usda.matching import lexical_match_score

_ALIAS_MAP = {
    "prime rib": "beef rib",
    "penne": "pasta",
}


def _required_tokens(requested: str) -> set[str]:
    tokens = set(requested.split())
    optional = {"beef", "pork", "chicken", "turkey", "duck", "fish", "snapper", "milk", "bread", "rice", "pasta", "mustard", "bagel", "cream", "bell", "pepper", "pea", "grapefruit", "lemon"}
    return {token for token in tokens if token not in optional}


def _has_required_tokens(description: str, requested: str) -> bool:
    required = _required_tokens(requested)
    if not required:
        return True
    lowered = description.lower()
    return all(token in lowered for token in required)


def refine_specific_candidates(frame: pd.DataFrame, ingredient: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    ingredient = ingredient.lower().strip()
    descriptions = frame["description"].astype(str).str.lower()
    mask = pd.Series([True] * len(frame), index=frame.index)
    if ingredient == "bread":
        mask &= descriptions.str.contains("bread") & ~descriptions.str.contains("potato|cinnamon")
    elif ingredient == "cream":
        mask &= descriptions.str.contains("cream") & ~descriptions.str.contains("cheese")
    elif ingredient == "milk":
        mask &= descriptions.str.contains("milk") & ~descriptions.str.contains("sheep|mozzarella|whole milk")
    elif ingredient == "lowfat milk":
        mask &= descriptions.str.contains("lowfat") & ~descriptions.str.contains("buttermilk")
    elif ingredient == "milk whole":
        mask &= descriptions.str.contains("milk, whole") & ~descriptions.str.contains("cheese")
    elif ingredient == "peach juice":
        mask &= descriptions.str.contains("peach juice")
    elif ingredient == "rice white":
        mask &= descriptions.str.contains("rice, white") & ~descriptions.str.contains("flour")
    elif ingredient == "sardine":
        mask &= ~descriptions.str.contains("oil")
    elif ingredient == "mustard":
        mask &= descriptions.str.contains("mustard") & ~descriptions.str.contains("oil")
    elif ingredient == "bagel":
        mask &= descriptions.str.contains("bagels?") & ~descriptions.str.contains("chips")
    filtered = frame.loc[mask]
    return filtered if not filtered.empty else frame


def _preference_bonus(description: str, ingredient: str) -> int:
    lowered = description.lower()
    bonus = 0
    if ingredient in {"chicken leg", "snapper"} and "cooked" in lowered:
        bonus += 5
    if ingredient == "pork" and "composite of trimmed retail cuts" in lowered:
        bonus += 6
    if ingredient in {"bell pepper", "lemon", "pea", "grapefruit"} and "raw" in lowered:
        bonus += 5
    if ingredient == "lemon" and "juice" in lowered:
        bonus -= 6
    if ingredient == "grapefruit" and "juice" in lowered:
        bonus -= 6
    if ingredient == "pea" and "cooked" in lowered:
        bonus -= 3
    if ingredient == "bell pepper" and "sauteed" in lowered:
        bonus -= 3
    return bonus


def select_representative_fdc(food_catalog: pd.DataFrame, ingredient: str) -> dict | None:
    if food_catalog.empty or "description" not in food_catalog.columns:
        return None
    requested = _ALIAS_MAP.get(ingredient.lower().strip(), ingredient.lower().strip())
    candidates = refine_specific_candidates(food_catalog.copy(), requested)

    if "normalized_ingredient" in candidates.columns:
        normalized_matches = candidates[candidates["normalized_ingredient"].astype(str).str.lower() == requested].copy()
        if not normalized_matches.empty:
            normalized_matches["score"] = normalized_matches["description"].astype(str).map(lambda desc: lexical_match_score(requested, desc))
            normalized_matches["pref"] = normalized_matches["description"].astype(str).map(lambda desc: _preference_bonus(desc, requested))
            normalized_matches = normalized_matches.sort_values(by=["pref", "score"], ascending=False)
            row = normalized_matches.iloc[0].to_dict()
            row["match_method"] = "normalized_bank"
            row["semantic_score"] = 1.0
            return row

    candidates = candidates.copy()
    candidates = candidates[candidates["description"].astype(str).map(lambda desc: _has_required_tokens(desc, requested))]
    if candidates.empty:
        return None
    candidates["score"] = candidates["description"].astype(str).map(lambda desc: lexical_match_score(requested, desc))
    candidates = candidates[candidates["score"] > 0.0]
    if candidates.empty:
        return None
    candidates["pref"] = candidates["description"].astype(str).map(lambda desc: _preference_bonus(desc, requested))
    candidates = candidates.sort_values(by=["pref", "score"], ascending=False)
    row = candidates.iloc[0].to_dict()
    row["match_method"] = "lexical"
    row["semantic_score"] = round(float(row.get("score", 0.0)), 6)
    return row
