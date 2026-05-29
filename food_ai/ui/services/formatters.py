from __future__ import annotations

from typing import Any

from food_ai.ui.services.runtime_service import format_runtime_status_for_ui


LOW_CONFIDENCE_THRESHOLD = 0.45
HIGH_CONFIDENCE_THRESHOLD = 0.65
NUTRIENT_KEYS = (
    "calories",
    "protein_g",
    "total_fat_g",
    "total_carbs_g",
    "fiber_g",
    "total_sugars_g",
    "sodium_mg",
    "cholesterol_mg",
)


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{max(0.0, min(1.0, float(value))) * 100:.1f}%"


def _round(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _table_rows(items: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        row = {}
        for key in keys:
            row[key] = item.get(key)
        rows.append(row)
    return rows


def _normalization_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return _table_rows(
            value,
            ["raw_ingredient", "canonical", "usda_match_label", "usda_match_method", "embedding_backend", "confidence"],
        )
    if not isinstance(value, dict):
        return []
    rows = []
    for item in value.get("matched") or []:
        rows.append(
            {
                "raw_ingredient": item.get("raw_ingredient"),
                "normalized_ingredient": item.get("normalized") or item.get("canonical"),
                "canonical": item.get("canonical"),
                "matched_ingredient": item.get("matched_ingredient") or item.get("canonical"),
                "usda_match_label": item.get("usda_match_label"),
                "usda_match_method": item.get("usda_match_method"),
                "embedding_backend": item.get("embedding_backend"),
                "confidence": item.get("confidence"),
                "weight_source": item.get("weight_source"),
                "original_frequency_weight": item.get("original_frequency_weight"),
                "mass_weight": item.get("mass_weight"),
                "median_grams": item.get("median_grams"),
                "nutrition5k_match_count": item.get("nutrition5k_match_count"),
                "nutrition_source": item.get("nutrition_source"),
                "included_in_nutrition": item.get("included_in_nutrition", True),
                "exclusion_reason": item.get("exclusion_reason"),
                "status": "matched",
            }
        )
    for item in value.get("unmatched") or []:
        rows.append(
            {
                "raw_ingredient": item.get("ingredient"),
                "normalized_ingredient": item.get("normalized") or item.get("ingredient"),
                "canonical": item.get("ingredient"),
                "matched_ingredient": item.get("matched_ingredient"),
                "usda_match_label": None,
                "usda_match_method": item.get("reason", "not_found"),
                "embedding_backend": "none",
                "confidence": None,
                "weight_source": item.get("weight_source"),
                "original_frequency_weight": item.get("original_frequency_weight"),
                "mass_weight": item.get("mass_weight"),
                "median_grams": item.get("median_grams"),
                "nutrition5k_match_count": item.get("nutrition5k_match_count"),
                "nutrition_source": item.get("nutrition_source"),
                "included_in_nutrition": item.get("included_in_nutrition", True),
                "exclusion_reason": item.get("exclusion_reason"),
                "status": "unmatched",
            }
        )
    return rows


def _ingredient_rows(
    items: list[dict[str, Any]],
    normalization_rows: list[dict[str, Any]],
    bridge_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalization_by_raw = {
        str(item.get("raw_ingredient") or item.get("canonical") or "").strip().lower(): item
        for item in normalization_rows
    }
    bridge_by_raw = {
        str(item.get("ingredient") or item.get("raw_ingredient") or "").strip().lower(): item
        for item in bridge_rows
    }
    rows: list[dict[str, Any]] = []
    for item in items:
        ingredient = str(item.get("ingredient") or "")
        normalization = normalization_by_raw.get(ingredient.strip().lower(), {})
        bridge = bridge_by_raw.get(ingredient.strip().lower(), {})
        status = bridge.get("status") or normalization.get("status")
        exclusion_reason = item.get("exclusion_reason") or bridge.get("exclusion_reason") or normalization.get("exclusion_reason")
        reason = exclusion_reason
        if not reason and status not in {None, "found", "matched"}:
            reason = bridge.get("reason") or normalization.get("usda_match_method")
        contributions = bridge.get("nutrient_contributions") or {}
        contribution_columns = {
            f"{key}_contrib": contributions.get(f"{key}_contrib", 0.0)
            for key in NUTRIENT_KEYS
        }
        rows.append(
            {
                "include_in_nutrition": item.get(
                    "included_in_nutrition",
                    bridge.get("included_in_nutrition", normalization.get("included_in_nutrition", True)),
                ),
                "ingredient": item.get("ingredient"),
                "normalized_ingredient": item.get("normalized") or bridge.get("normalized") or normalization.get("normalized_ingredient") or normalization.get("canonical"),
                "matched_ingredient": bridge.get("matched_ingredient") or normalization.get("matched_ingredient"),
                "status": status,
                "weight": item.get("weight"),
                "grams_default": item.get("grams_default") or bridge.get("grams_default"),
                "original_grams_per_100g_dish": item.get("original_grams_per_100g_dish") or bridge.get("original_grams_per_100g_dish") or item.get("grams_per_100g_dish") or bridge.get("grams_per_100g_dish"),
                "grams_per_100g_dish": item.get("grams_per_100g_dish") or bridge.get("grams_per_100g_dish"),
                "adjusted_grams_after_exclusions": bridge.get("adjusted_grams_after_exclusions"),
                "renormalization_factor": bridge.get("renormalization_factor", 1.0),
                "portion_method": item.get("portion_method") or bridge.get("portion_method"),
                "weight_source": item.get("weight_source"),
                "source_count": item.get("source_count"),
                "recipe_pct": item.get("recipe_pct"),
                "ingredient_role": item.get("ingredient_role") or bridge.get("ingredient_role"),
                "canonical_source_type": bridge.get("canonical_source_type"),
                "matched_fdc_id": bridge.get("fdc_id"),
                "matched_source": bridge.get("source"),
                "confidence": bridge.get("confidence", normalization.get("confidence")),
                "match_method": bridge.get("match_method") or normalization.get("usda_match_method"),
                "reason": reason,
                "calories_contribution": (bridge.get("nutrient_contributions") or {}).get("calories_contrib", 0.0),
                "protein_contribution": (bridge.get("nutrient_contributions") or {}).get("protein_g_contrib", 0.0),
                "fat_contribution": (bridge.get("nutrient_contributions") or {}).get("total_fat_g_contrib", 0.0),
                "carbs_contribution": (bridge.get("nutrient_contributions") or {}).get("total_carbs_g_contrib", 0.0),
                **contribution_columns,
            }
        )
    return rows


def _build_statuses(
    *,
    top_dish: dict[str, Any] | None,
    overall_confidence: float | None,
    warnings: list[str],
    metadata: dict[str, Any],
    palm_ready: dict[str, Any],
    visible_ingredients: list[dict[str, Any]],
    all_ingredients: list[dict[str, Any]],
) -> list[dict[str, str]]:
    statuses = [{"label": "food / non-food gate unavailable", "level": "info"}]

    if top_dish is not None:
        if overall_confidence is not None and overall_confidence >= HIGH_CONFIDENCE_THRESHOLD:
            statuses.append({"label": "food detected (heuristic)", "level": "success"})
        elif overall_confidence is not None and overall_confidence < LOW_CONFIDENCE_THRESHOLD:
            statuses.append({"label": "low confidence", "level": "warning"})
        else:
            statuses.append({"label": "dish candidate found", "level": "info"})
    else:
        statuses.append({"label": "no dish candidate", "level": "warning"})

    if metadata.get("inference_mode") == "retrieval_fallback":
        statuses.append({"label": "retrieval fallback used", "level": "warning"})
    if not metadata.get("classifier_checkpoint_exists", True):
        statuses.append({"label": "classifier artifact missing", "level": "warning"})
    if warnings:
        statuses.append({"label": "degraded-mode fallbacks used", "level": "warning"})
    if all_ingredients and len(visible_ingredients) < len(all_ingredients):
        statuses.append({"label": "ingredient inference partial", "level": "info"})
    if palm_ready.get("status") != "ready":
        statuses.append({"label": "nutrition bridge unavailable", "level": "warning"})
    else:
        statuses.append({"label": "nutrition bridge ready", "level": "success"})
    return statuses


def format_result_for_ui(result: dict[str, Any]) -> dict[str, Any]:
    fused = result.get("dish_fused_topk") or []
    top_dish = fused[0] if fused else None
    visible_ingredients = result.get("visible_ingredients") or []
    all_ingredients = result.get("ingredients_ranked") or []
    hidden_ingredients = [item for item in all_ingredients if str(item.get("visibility_label", "")).startswith("hidden")]
    recipes = result.get("candidate_recipes") or []
    palm_ready = result.get("palm_ready") or {}
    confidence = result.get("confidence") or {}
    runtime = format_runtime_status_for_ui(result.get("runtime_diagnostics") or {})
    usda_status = result.get("usda_status") or {}
    ingredient_normalization = result.get("ingredient_normalization") or []
    metadata = result.get("metadata") or {}

    top_dish_name = top_dish.get("name") if top_dish else "No dish candidate"
    overall_confidence = confidence.get("overall")
    ready_state = palm_ready.get("status", "unknown")
    warnings = result.get("warnings") or []
    summary_sentence = (
        f"Top dish: {top_dish_name}. Overall confidence {_pct(overall_confidence)}. "
        f"PALM bridge status: {ready_state}."
    )
    if warnings:
        summary_sentence += f" Warnings: {', '.join(warnings)}."

    estimated_nutrition = palm_ready.get("estimated_nutrition_per_100g") or {}
    nutrition_summary = {
        "calories": estimated_nutrition.get("calories"),
        "protein_g": estimated_nutrition.get("protein_g"),
        "fat_g": estimated_nutrition.get("total_fat_g", estimated_nutrition.get("fat_g")),
        "carbs_g": estimated_nutrition.get("total_carbs_g", estimated_nutrition.get("carbs_g")),
        "fiber_g": estimated_nutrition.get("fiber_g"),
        "total_sugars_g": estimated_nutrition.get("total_sugars_g"),
        "sodium_mg": estimated_nutrition.get("sodium_mg"),
        "cholesterol_mg": estimated_nutrition.get("cholesterol_mg"),
    }
    nutrition_weighting = {
        "label": palm_ready.get("label"),
        "requested_weight_source": palm_ready.get("requested_weight_source"),
        "actual_weight_source": palm_ready.get("actual_weight_source"),
        "dish_mass_priors_requested": palm_ready.get("dish_mass_priors_requested"),
        "dish_mass_prior_applied_ingredient_count": palm_ready.get("dish_mass_prior_applied_ingredient_count"),
        "nutrition5k_requested": palm_ready.get("nutrition5k_requested"),
        "nutrition5k_matched_dish_count": palm_ready.get("nutrition5k_matched_dish_count"),
        "nutrition5k_applied_ingredient_count": palm_ready.get("nutrition5k_applied_ingredient_count"),
    }
    statuses = _build_statuses(
        top_dish=top_dish,
        overall_confidence=_round(overall_confidence),
        warnings=warnings,
        metadata=metadata,
        palm_ready=palm_ready,
        visible_ingredients=visible_ingredients,
        all_ingredients=all_ingredients,
    )

    normalization_rows = _normalization_rows(ingredient_normalization)
    bridge_ingredient_rows = (palm_ready.get("matched_ingredients") or []) + (palm_ready.get("unmatched_ingredients") or [])

    return {
        "summary": {
            "top_dish": top_dish_name,
            "overall_confidence": _pct(overall_confidence),
            "ready_state": ready_state,
            "summary_sentence": summary_sentence,
        },
        "statuses": statuses,
        "pipeline": {
            "gate": {
                "label": "unavailable",
                "available": False,
            },
            "gate_reason": "No dedicated food / non-food gate is implemented in the current maintained pipeline.",
            "inference_mode": metadata.get("inference_mode", "unknown"),
            "selected_dish": top_dish_name,
            "selected_dish_score": _round(top_dish.get("score") if top_dish else None),
            "selected_dish_source": top_dish.get("source") if top_dish else None,
            "warnings": warnings,
            "fallbacks_used": (result.get("dish_fusion") or {}).get("fallbacks_used") or [],
            "classifier_checkpoint_exists": metadata.get("classifier_checkpoint_exists"),
            "calibration_artifact_exists": metadata.get("calibration_artifact_exists"),
            "classifier_available": metadata.get("classifier_available"),
            "artifacts_note": (
                "Classifier checkpoint is missing; the pipeline may rely heavily on retrieval fallback."
                if not metadata.get("classifier_checkpoint_exists", True)
                else None
            ),
        },
        "dishes": {
            "selected": {
                "name": top_dish_name,
                "score": _round(top_dish.get("score") if top_dish else None),
                "source": top_dish.get("source") if top_dish else None,
                "inference_mode": metadata.get("inference_mode", "unknown"),
            },
            "fused": _table_rows(fused, ["name", "score", "source"]),
            "classifier": _table_rows(result.get("dish_classifier_topk") or [], ["name", "score", "source"]),
            "retrieval": _table_rows(result.get("retrieval_candidates") or [], ["name", "score", "source"]),
            "classifier_calibrated": bool(result.get("dish_classifier_calibrated")),
            "classifier_temperature": _round(result.get("dish_classifier_temperature")),
        },
        "ingredients": {
            "visible": _ingredient_rows(visible_ingredients, normalization_rows, bridge_ingredient_rows),
            "ranked": _ingredient_rows(all_ingredients, normalization_rows, bridge_ingredient_rows),
            "hidden": _ingredient_rows(hidden_ingredients, normalization_rows, bridge_ingredient_rows),
            "normalization": normalization_rows,
        },
        "recipes": _table_rows(recipes, ["title", "score", "matched_dish", "title_score", "image_score"]),
        "nutrition": {
            "summary": nutrition_summary,
            "weighting": nutrition_weighting,
            "payload": palm_ready,
            "recalc": result.get("nutrition_recalc") or palm_ready.get("nutrition_recalc") or {},
        },
        "debug": {
            "warnings": warnings,
            "fusion": result.get("dish_fusion") or {},
            "segmentation": result.get("segmentation") or {},
            "metadata": metadata,
            "runtime": runtime,
            "usda_status": usda_status,
            "usda_portion_audit": result.get("usda_portion_audit") or {},
            "dish_variant_evidence": ((palm_ready.get("db_dish_prior_summary") or {}).get("dish_variant_evidence") or []),
            "ingredient_normalization": ingredient_normalization,
            "debug": result.get("debug") or {},
            "raw_result": result,
        },
    }
