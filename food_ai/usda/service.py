from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from food_ai.config import DATA_DIR
from food_ai.sync.usda_ingredient_nutrition_loader import select_representative_fdc
from food_ai.usda.embeddings import DEFAULT_EMBEDDINGS_PATH, MODEL_NAME, build_or_load_usda_embeddings, cosine_similarity_search, load_usda_embedder
from food_ai.usda.source import load_usda_food_catalog
from food_ai.utils.ingredient_normalize import normalize_ingredient_with_metadata


logger = logging.getLogger(__name__)

DEFAULT_USDA_SOURCE_PATH = DATA_DIR / "USDA" / "food.csv"


@lru_cache(maxsize=2)
def get_usda_food_catalog(csv_path: str | Path = DEFAULT_USDA_SOURCE_PATH) -> pd.DataFrame:
    return load_usda_food_catalog(csv_path)


def _base_result(normalization: dict[str, Any], backend_info: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = dict(normalization)
    result.update(
        {
            "usda_match_method": "none",
            "usda_match_id": None,
            "usda_match_label": None,
            "embedding_backend": backend_info.get("backend"),
            "embedding_fallback_reason": backend_info.get("fallback_reason"),
            "confidence": 0.0,
        }
    )
    return result


@lru_cache(maxsize=4096)
def _match_canonical_ingredient_cached(
    normalized: str,
    csv_path: str,
    embeddings_path: str,
    model_name: str,
    local_files_only: bool,
) -> dict[str, Any]:
    catalog = get_usda_food_catalog(csv_path)
    direct = select_representative_fdc(catalog, normalized)
    if direct is not None:
        return {
            "normalization_status": "matched",
            "usda_match_method": str(direct.get("match_method") or "lexical"),
            "usda_match_id": int(direct.get("fdc_id")) if direct.get("fdc_id") is not None else None,
            "usda_match_label": str(direct.get("description") or ""),
            "confidence": round(float(direct.get("semantic_score") or direct.get("score") or 0.0), 4),
        }

    payload = build_or_load_usda_embeddings(
        source_path=csv_path,
        output_path=embeddings_path,
        model_name=model_name,
        local_files_only=local_files_only,
        force_rebuild=False,
    )
    matches = cosine_similarity_search(normalized, payload, top_k=1, local_files_only=local_files_only)
    if matches:
        top_match = matches[0]
        return {
            "normalization_status": "matched",
            "usda_match_method": "semantic",
            "usda_match_id": int(top_match.get("fdc_id")) if top_match.get("fdc_id") is not None else None,
            "usda_match_label": str(top_match.get("description") or ""),
            "confidence": round(float(top_match.get("score") or 0.0), 4),
        }

    return {
        "normalization_status": "unmatched",
        "usda_match_method": "none",
        "usda_match_id": None,
        "usda_match_label": None,
        "confidence": 0.0,
    }


def _match_canonical_ingredient(
    normalized: str,
    catalog: pd.DataFrame,
    csv_path: str | Path,
    embeddings_path: str | Path,
    model_name: str,
    local_files_only: bool,
) -> dict[str, Any]:
    del catalog
    return _match_canonical_ingredient_cached(
        normalized=str(normalized),
        csv_path=str(csv_path),
        embeddings_path=str(embeddings_path),
        model_name=str(model_name),
        local_files_only=bool(local_files_only),
    )


def match_ingredient_to_usda(
    raw_ingredient: str,
    csv_path: str | Path = DEFAULT_USDA_SOURCE_PATH,
    embeddings_path: str | Path = DEFAULT_EMBEDDINGS_PATH,
    model_name: str = MODEL_NAME,
    local_files_only: bool = True,
) -> dict[str, Any]:
    normalization = normalize_ingredient_with_metadata(raw_ingredient)
    catalog = get_usda_food_catalog(csv_path)
    backend_info = load_usda_embedder(model_name=model_name, local_files_only=local_files_only)
    result = _base_result(normalization, backend_info)

    if catalog.empty:
        result["normalization_status"] = "unmatched"
        return result

    normalized = normalization.get("canonical")
    component_metadata = normalization.get("components") or []
    if normalization.get("normalization_status") in {"normalized_multi_fragment", "partially_normalized_multi_fragment"}:
        component_matches: list[dict[str, Any]] = []
        matched_count = 0
        matchable_count = 0
        for component in component_metadata:
            component_result = dict(component)
            component_result.update(
                {
                    "usda_match_method": "none",
                    "usda_match_id": None,
                    "usda_match_label": None,
                    "embedding_backend": backend_info.get("backend"),
                    "embedding_fallback_reason": backend_info.get("fallback_reason"),
                    "confidence": 0.0,
                }
            )
            component_canonical = component.get("canonical")
            if component_canonical:
                matchable_count += 1
                component_result.update(
                    _match_canonical_ingredient(
                        str(component_canonical),
                        catalog,
                        csv_path,
                        embeddings_path,
                        model_name,
                        local_files_only,
                    )
                )
                if component_result.get("usda_match_id") is not None:
                    matched_count += 1
            component_matches.append(component_result)

        result["component_matches"] = component_matches
        result["usda_match_method"] = "multi_fragment"
        matched_labels = [str(item.get("usda_match_label")) for item in component_matches if item.get("usda_match_label")]
        if matched_labels:
            result["usda_match_label"] = "; ".join(matched_labels)
        matched_ids = [int(item["usda_match_id"]) for item in component_matches if item.get("usda_match_id") is not None]
        if matched_ids:
            result["usda_match_id"] = matched_ids[0]
        matched_confidences = [float(item.get("confidence") or 0.0) for item in component_matches if item.get("usda_match_id") is not None]
        if matched_confidences:
            result["confidence"] = round(sum(matched_confidences) / len(matched_confidences), 4)
        if matched_count and matched_count == matchable_count and matchable_count > 0:
            result["normalization_status"] = "matched_multi_fragment"
        elif matched_count:
            result["normalization_status"] = "partial_multi_fragment_match"
        else:
            result["normalization_status"] = "unmatched_multi_fragment"
        return result

    if not normalized:
        result["normalization_status"] = "unmatched"
        return result

    result.update(
        _match_canonical_ingredient(
            str(normalized),
            catalog,
            csv_path,
            embeddings_path,
            model_name,
            local_files_only,
        )
    )
    if result.get("usda_match_id") is None:
        logger.info("No USDA match found for ingredient=%s canonical=%s", raw_ingredient, normalized)
    return result


def inspect_ingredient_matches(
    ingredients: list[dict[str, Any]],
    sample_size: int = 8,
    csv_path: str | Path = DEFAULT_USDA_SOURCE_PATH,
    embeddings_path: str | Path = DEFAULT_EMBEDDINGS_PATH,
    local_files_only: bool = True,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for ingredient_record in ingredients[: max(1, sample_size)]:
        ingredient_name = str(ingredient_record.get("ingredient") or "").strip()
        if not ingredient_name:
            continue
        match = match_ingredient_to_usda(
            ingredient_name,
            csv_path=csv_path,
            embeddings_path=embeddings_path,
            local_files_only=local_files_only,
        )
        match["ranked_confidence"] = ingredient_record.get("confidence")
        match["visibility_label"] = ingredient_record.get("visibility_label")
        samples.append(match)
    return samples
