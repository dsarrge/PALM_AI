from __future__ import annotations

import copy
from typing import Any


DEFAULT_STD_GUARDRAIL_THRESHOLD_G = 12.5
DEFAULT_MODE_MEAN_DELTA_G = 3.0
DEFAULT_MODE_MEAN_RATIO = 0.80


def _as_float(value: Any) -> float | None:
    if value in (None, "", "n/a"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mode_guardrail_applies(
    *,
    mean_g: float | None,
    mode_g: float | None,
    std_g: float | None,
    std_threshold_g: float,
    fallback_delta_g: float,
    fallback_ratio: float,
) -> tuple[bool, str | None]:
    if mean_g is None or mean_g <= 0.0 or mode_g is None or mode_g <= 0.0:
        return False, None

    # Guardrails should reduce outlier-heavy means, not increase an ingredient's weight.
    if mode_g >= mean_g:
        return False, None

    delta_g = mean_g - mode_g
    if std_g is not None and std_g >= std_threshold_g and delta_g >= fallback_delta_g:
        return True, "std_above_threshold_use_mode"

    # Backward-compatible fallback for current PALM_V2 DBs that expose mean/mode
    # but do not yet store std. This is intentionally conservative.
    if std_g is None and delta_g >= fallback_delta_g and (mode_g / mean_g) <= fallback_ratio:
        return True, "mean_mode_spread_proxy_use_mode"

    return False, None


def _guardrail_row(
    row: dict[str, Any],
    *,
    std_threshold_g: float,
    fallback_delta_g: float,
    fallback_ratio: float,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    updated = dict(row)
    mean_g = _as_float(updated.get("grams_mean") or updated.get("mean_grams_per_100g_dish") or updated.get("grams_per_100g_dish"))
    mode_g = _as_float(updated.get("grams_mode") or updated.get("mode_grams_per_100g_dish"))
    median_g = _as_float(updated.get("grams_median") or updated.get("median_grams_per_100g_dish"))
    std_g = _as_float(updated.get("grams_std") or updated.get("std_grams_per_100g_dish"))

    applies, reason = _mode_guardrail_applies(
        mean_g=mean_g,
        mode_g=mode_g,
        std_g=std_g,
        std_threshold_g=std_threshold_g,
        fallback_delta_g=fallback_delta_g,
        fallback_ratio=fallback_ratio,
    )
    if not applies or mode_g is None:
        return updated, None

    original_g = _as_float(updated.get("grams_per_100g_dish") or updated.get("grams_default") or mean_g)
    selected_g = mode_g
    updated["grams_per_100g_dish"] = round(selected_g, 6)
    updated["grams_default"] = round(selected_g, 6)
    updated["weight_guardrail_applied"] = True
    updated["weight_guardrail_reason"] = reason
    updated["weight_guardrail_original_grams_per_100g_dish"] = original_g
    updated["weight_guardrail_selected_metric"] = "mode_grams_per_100g_dish"
    updated["weight_guardrail_threshold_g"] = std_threshold_g
    if "portion_method" in updated:
        updated["portion_method"] = f"{updated.get('portion_method')}_mode_guardrail"

    audit = {
        "ingredient": updated.get("ingredient") or updated.get("matched_ingredient"),
        "matched_ingredient": updated.get("matched_ingredient"),
        "reason": reason,
        "original_grams_per_100g_dish": original_g,
        "selected_grams_per_100g_dish": round(selected_g, 6),
        "mean_grams_per_100g_dish": mean_g,
        "median_grams_per_100g_dish": median_g,
        "mode_grams_per_100g_dish": mode_g,
        "std_grams_per_100g_dish": std_g,
        "std_threshold_g": std_threshold_g,
    }
    return updated, audit


def _apply_to_rows(
    rows: list[dict[str, Any]],
    *,
    std_threshold_g: float,
    fallback_delta_g: float,
    fallback_ratio: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    updated_rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for row in rows:
        updated, audit = _guardrail_row(
            row,
            std_threshold_g=std_threshold_g,
            fallback_delta_g=fallback_delta_g,
            fallback_ratio=fallback_ratio,
        )
        updated_rows.append(updated)
        if audit is not None:
            audits.append(audit)
    return updated_rows, audits


def apply_ingredient_weight_guardrails(
    inference_result: dict[str, Any],
    *,
    std_threshold_g: float = DEFAULT_STD_GUARDRAIL_THRESHOLD_G,
    fallback_delta_g: float = DEFAULT_MODE_MEAN_DELTA_G,
    fallback_ratio: float = DEFAULT_MODE_MEAN_RATIO,
) -> dict[str, Any]:
    """Apply conservative runtime guardrails to volatile ingredient gram defaults.

    The preferred rule is standard-deviation based: when std is available and high,
    use mode grams instead of mean grams. Current PALM_V2 DBs may not yet expose std,
    so a conservative mean/mode spread proxy is used until the DB is rebuilt with std.
    """
    result = copy.deepcopy(inference_result)
    all_audits: list[dict[str, Any]] = []

    for key in ("ingredients_ranked", "visible_ingredients"):
        rows = result.get(key) or []
        if not isinstance(rows, list):
            continue
        updated_rows, audits = _apply_to_rows(
            rows,
            std_threshold_g=std_threshold_g,
            fallback_delta_g=fallback_delta_g,
            fallback_ratio=fallback_ratio,
        )
        result[key] = updated_rows
        all_audits.extend(audits)

    palm_ready = dict(result.get("palm_ready") or {})
    for key in ("matched_ingredients", "unmatched_ingredients"):
        rows = palm_ready.get(key) or []
        if not isinstance(rows, list):
            continue
        updated_rows, audits = _apply_to_rows(
            rows,
            std_threshold_g=std_threshold_g,
            fallback_delta_g=fallback_delta_g,
            fallback_ratio=fallback_ratio,
        )
        palm_ready[key] = updated_rows
        all_audits.extend(audits)

    if all_audits:
        deduped: dict[tuple[str, float | None], dict[str, Any]] = {}
        for audit in all_audits:
            key = (str(audit.get("ingredient") or audit.get("matched_ingredient") or ""), audit.get("original_grams_per_100g_dish"))
            deduped[key] = audit
        palm_ready["ingredient_weight_guardrails"] = {
            "applied_count": len(deduped),
            "std_threshold_g": std_threshold_g,
            "fallback_delta_g": fallback_delta_g,
            "fallback_ratio": fallback_ratio,
            "rule": "use mode grams when observed grams are high variance and mode is lower than mean",
            "audits": list(deduped.values()),
        }
        warnings = list(result.get("warnings") or [])
        warnings.append("ingredient_weight_guardrail_applied")
        result["warnings"] = list(dict.fromkeys(warnings))

    result["palm_ready"] = palm_ready
    return result


__all__ = [
    "DEFAULT_MODE_MEAN_DELTA_G",
    "DEFAULT_MODE_MEAN_RATIO",
    "DEFAULT_STD_GUARDRAIL_THRESHOLD_G",
    "apply_ingredient_weight_guardrails",
]
