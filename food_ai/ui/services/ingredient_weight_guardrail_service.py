from __future__ import annotations

import copy
from typing import Any

from food_ai.ui.services.ingredient_gram_override_service import apply_gram_overrides_to_result


DEFAULT_INGREDIENT_WEIGHT_POLICY = "mode"
DEFAULT_STD_GUARDRAIL_THRESHOLD_G = 12.5
DEFAULT_MODE_MEAN_DELTA_G = 3.0
DEFAULT_MODE_MEAN_RATIO = 0.80
SUPPORTED_INGREDIENT_WEIGHT_POLICIES = ("mode", "mean", "guardrail")


def _as_float(value: Any) -> float | None:
    if value in (None, "", "n/a"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ui_gram_edits() -> dict[str, Any]:
    try:
        import streamlit as st
    except Exception:
        return {}
    try:
        return dict(st.session_state.get("ingredient_gram_overrides") or {})
    except Exception:
        return {}


def _row_metrics(row: dict[str, Any]) -> dict[str, float | None]:
    current_g = _as_float(row.get("grams_per_100g_dish") or row.get("grams_default"))
    mean_g = _as_float(row.get("grams_mean") or row.get("mean_grams_per_100g_dish")) or current_g
    return {
        "current": current_g,
        "mean": mean_g,
        "mode": _as_float(row.get("grams_mode") or row.get("mode_grams_per_100g_dish")),
        "median": _as_float(row.get("grams_median") or row.get("median_grams_per_100g_dish")),
        "std": _as_float(row.get("grams_std") or row.get("std_grams_per_100g_dish")),
    }


def _guardrail_mode_applies(
    *,
    mean_g: float | None,
    mode_g: float | None,
    std_g: float | None,
    std_threshold_g: float,
    fallback_delta_g: float,
    fallback_ratio: float,
) -> bool:
    if mean_g is None or mean_g <= 0.0 or mode_g is None or mode_g <= 0.0 or mode_g >= mean_g:
        return False
    delta_g = mean_g - mode_g
    if std_g is not None:
        return std_g >= std_threshold_g and delta_g >= fallback_delta_g
    return delta_g >= fallback_delta_g and (mode_g / mean_g) <= fallback_ratio


def _select_grams(
    metrics: dict[str, float | None],
    *,
    policy: str,
    std_threshold_g: float,
    fallback_delta_g: float,
    fallback_ratio: float,
) -> tuple[float | None, str | None, str | None]:
    mean_g = metrics["mean"]
    mode_g = metrics["mode"]
    std_g = metrics["std"]
    if policy == "mean":
        return (mean_g, "mean_grams_per_100g_dish", "policy_mean") if mean_g and mean_g > 0.0 else (None, None, None)
    if policy == "mode":
        if mode_g and mode_g > 0.0:
            return mode_g, "mode_grams_per_100g_dish", "policy_mode"
        return (mean_g, "mean_grams_per_100g_dish", "mode_missing_fallback_mean") if mean_g and mean_g > 0.0 else (None, None, None)
    if policy == "guardrail" and _guardrail_mode_applies(
        mean_g=mean_g,
        mode_g=mode_g,
        std_g=std_g,
        std_threshold_g=std_threshold_g,
        fallback_delta_g=fallback_delta_g,
        fallback_ratio=fallback_ratio,
    ):
        return mode_g, "mode_grams_per_100g_dish", "high_variance_mode_guardrail"
    return None, None, None


def _apply_policy_to_row(
    row: dict[str, Any],
    *,
    policy: str,
    std_threshold_g: float,
    fallback_delta_g: float,
    fallback_ratio: float,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    updated = dict(row)
    metrics = _row_metrics(updated)
    selected_g, selected_metric, reason = _select_grams(
        metrics,
        policy=policy,
        std_threshold_g=std_threshold_g,
        fallback_delta_g=fallback_delta_g,
        fallback_ratio=fallback_ratio,
    )
    if selected_g is None or selected_metric is None or reason is None:
        updated["ingredient_weight_policy"] = policy
        return updated, None
    current_g = metrics["current"]
    if current_g is not None and abs(current_g - selected_g) < 0.0005:
        updated["ingredient_weight_policy"] = policy
        return updated, None
    updated["grams_per_100g_dish"] = round(selected_g, 6)
    updated["grams_default"] = round(selected_g, 6)
    updated["ingredient_weight_policy"] = policy
    updated["weight_guardrail_applied"] = policy == "guardrail"
    updated["weight_guardrail_reason"] = reason
    updated["weight_guardrail_original_grams_per_100g_dish"] = current_g
    updated["weight_guardrail_selected_metric"] = selected_metric
    if "portion_method" in updated:
        updated["portion_method"] = f"{updated.get('portion_method')}_{policy}"
    return updated, {
        "ingredient": updated.get("ingredient") or updated.get("matched_ingredient"),
        "matched_ingredient": updated.get("matched_ingredient"),
        "policy": policy,
        "reason": reason,
        "original_grams_per_100g_dish": current_g,
        "selected_grams_per_100g_dish": round(selected_g, 6),
        "selected_metric": selected_metric,
        "mean_grams_per_100g_dish": metrics["mean"],
        "median_grams_per_100g_dish": metrics["median"],
        "mode_grams_per_100g_dish": metrics["mode"],
        "std_grams_per_100g_dish": metrics["std"],
    }


def _apply_to_rows(rows: list[dict[str, Any]], **kwargs: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    updated_rows = []
    audits = []
    for row in rows:
        updated, audit = _apply_policy_to_row(row, **kwargs)
        updated_rows.append(updated)
        if audit is not None:
            audits.append(audit)
    return updated_rows, audits


def apply_ingredient_weight_guardrails(
    inference_result: dict[str, Any],
    *,
    policy: str = DEFAULT_INGREDIENT_WEIGHT_POLICY,
    std_threshold_g: float = DEFAULT_STD_GUARDRAIL_THRESHOLD_G,
    fallback_delta_g: float = DEFAULT_MODE_MEAN_DELTA_G,
    fallback_ratio: float = DEFAULT_MODE_MEAN_RATIO,
) -> dict[str, Any]:
    if policy not in SUPPORTED_INGREDIENT_WEIGHT_POLICIES:
        raise ValueError("Unsupported ingredient weight policy: " + str(policy))
    result = copy.deepcopy(inference_result)
    all_audits: list[dict[str, Any]] = []
    kwargs = {
        "policy": policy,
        "std_threshold_g": std_threshold_g,
        "fallback_delta_g": fallback_delta_g,
        "fallback_ratio": fallback_ratio,
    }
    for key in ("ingredients_ranked", "visible_ingredients"):
        rows = result.get(key) or []
        if isinstance(rows, list):
            result[key], audits = _apply_to_rows(rows, **kwargs)
            all_audits.extend(audits)
    palm_ready = dict(result.get("palm_ready") or {})
    for key in ("matched_ingredients", "unmatched_ingredients"):
        rows = palm_ready.get(key) or []
        if isinstance(rows, list):
            palm_ready[key], audits = _apply_to_rows(rows, **kwargs)
            all_audits.extend(audits)
    deduped = {}
    for audit in all_audits:
        key = (str(audit.get("ingredient") or audit.get("matched_ingredient") or ""), audit.get("selected_metric"))
        deduped[key] = audit
    palm_ready["ingredient_weight_policy"] = {
        "policy": policy,
        "applied_count": len(deduped),
        "std_threshold_g": std_threshold_g,
        "fallback_delta_g": fallback_delta_g,
        "fallback_ratio": fallback_ratio,
        "audits": list(deduped.values()),
    }
    if deduped:
        warnings = list(result.get("warnings") or [])
        warnings.append(f"ingredient_weight_policy_applied:{policy}")
        result["warnings"] = list(dict.fromkeys(warnings))
    result["palm_ready"] = palm_ready
    gram_edits = _ui_gram_edits()
    if gram_edits:
        result = apply_gram_overrides_to_result(result, gram_edits)
    return result


__all__ = [
    "DEFAULT_INGREDIENT_WEIGHT_POLICY",
    "DEFAULT_MODE_MEAN_DELTA_G",
    "DEFAULT_MODE_MEAN_RATIO",
    "DEFAULT_STD_GUARDRAIL_THRESHOLD_G",
    "SUPPORTED_INGREDIENT_WEIGHT_POLICIES",
    "apply_ingredient_weight_guardrails",
]
