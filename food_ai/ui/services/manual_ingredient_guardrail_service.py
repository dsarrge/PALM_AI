from __future__ import annotations

from typing import Any, Callable

from food_ai.food_bridge.ingredient_normalize import normalize_ingredient


PLAIN_STARCH_TOKENS = ("noodle", "noodles", "pasta", "spaghetti", "macaroni", "rice")
PROCESSED_SALTY_TOKENS = ("sauce", "cheese", "sausage", "bacon", "ham", "pepperoni", "broth", "stock", "gravy")

COOKED_LOOKUP_CANDIDATES = {
    "lasagna noodle": ("cooked lasagna noodles", "cooked pasta", "pasta cooked"),
    "pasta": ("cooked pasta", "pasta cooked"),
    "spaghetti": ("cooked spaghetti", "spaghetti cooked", "cooked pasta"),
    "macaroni": ("cooked macaroni", "macaroni cooked", "cooked pasta"),
    "rice": ("cooked rice", "rice cooked"),
}


def _as_float(value: Any) -> float | None:
    if value in (None, "", "n/a"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nutrition(lookup: dict[str, Any]) -> dict[str, Any]:
    return dict(lookup.get("nutrition_per_100g") or {})


def _ingredient_key(text: str) -> str:
    return normalize_ingredient(str(text or "")) or str(text or "").strip().lower()


def _is_plain_starch(text: str) -> bool:
    key = _ingredient_key(text)
    return any(token in key for token in PLAIN_STARCH_TOKENS) and not any(token in key for token in PROCESSED_SALTY_TOKENS)


def _plain_starch_implausible(lookup: dict[str, Any]) -> bool:
    nutrition = _nutrition(lookup)
    sodium = _as_float(nutrition.get("sodium_mg"))
    carbs = _as_float(nutrition.get("total_carbs_g"))
    calories = _as_float(nutrition.get("calories"))
    return bool(
        (sodium is not None and sodium > 500.0)
        or (carbs is not None and carbs > 90.0)
        or (calories is not None and calories > 450.0)
    )


def _plain_starch_safe(lookup: dict[str, Any]) -> bool:
    if lookup.get("status") != "found" or not lookup.get("nutrition_per_100g"):
        return False
    return not _plain_starch_implausible(lookup)


def _candidate_names(ingredient: str) -> tuple[str, ...]:
    key = _ingredient_key(ingredient)
    candidates: list[str] = []
    for token, names in COOKED_LOOKUP_CANDIDATES.items():
        if token in key:
            candidates.extend(names)
    if key not in candidates:
        candidates.append(key)
    deduped: list[str] = []
    seen: set[str] = set()
    for name in candidates:
        clean = str(name).strip()
        if clean and clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    return tuple(deduped)


def guard_manual_ingredient_lookup(
    ingredient: str,
    lookup: dict[str, Any],
    lookup_fn: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Protect manual ingredients from implausible exact matches.

    The known failure mode is plain cooked starches, especially lasagna noodles,
    matching a salted/composite row and producing thousands of mg sodium per 100g.
    For these rows, prefer a safe cooked lookup. If no safe cooked lookup exists,
    mark the manual ingredient unmatched rather than using the implausible row.
    """
    if not _is_plain_starch(ingredient):
        return lookup
    if _plain_starch_safe(lookup):
        return lookup

    tried: list[dict[str, Any]] = []
    for candidate in _candidate_names(ingredient):
        if candidate.strip().lower() == str(ingredient).strip().lower():
            continue
        candidate_lookup = lookup_fn(candidate)
        tried.append(
            {
                "candidate": candidate,
                "status": candidate_lookup.get("status"),
                "matched_ingredient": candidate_lookup.get("matched_ingredient"),
                "nutrition_per_100g": candidate_lookup.get("nutrition_per_100g"),
            }
        )
        if _plain_starch_safe(candidate_lookup):
            fixed = dict(candidate_lookup)
            fixed["manual_lookup_guardrail_applied"] = True
            fixed["manual_lookup_guardrail_reason"] = "plain_starch_implausible_original_lookup"
            fixed["manual_lookup_original"] = lookup
            fixed["manual_lookup_replacement_query"] = candidate
            return fixed

    blocked = dict(lookup)
    blocked["status"] = "not_found"
    blocked["nutrition_per_100g"] = None
    blocked["reason"] = "manual_plain_starch_lookup_implausible"
    blocked["manual_lookup_guardrail_applied"] = True
    blocked["manual_lookup_original"] = lookup
    blocked["manual_lookup_candidates_tried"] = tried
    return blocked


__all__ = ["guard_manual_ingredient_lookup"]
