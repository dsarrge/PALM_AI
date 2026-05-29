from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from food_ai.config import resolve_runtime_db_path
from food_ai.food_bridge.nutrition_bridge import build_food_bridge
from food_ai.food_bridge.usda_portions import audit_usda_portion_tables
from food_ai.ui.config import UISettings
from food_ai.ui.services.runtime_service import get_runtime_diagnostics
from food_ai.vision.predict_dish import predict_dish


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UIInferenceOptions:
    top_k_dishes: int = 5
    top_k_recipes: int = 8
    top_k_ingredients: int = 20
    show_debug: bool = True
    enable_segmentation: bool = False
    prefer_calibrated_probabilities: bool = True
    enable_embedding_cache: bool = True
    save_run_report: bool = True
    run_report_dir: str | Path | None = None
    use_dish_mass_priors: bool = False
    use_nutrition5k: bool = False
    local_files_only: bool = True
    developer_mode: bool = False

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]



def get_smoke_test_image(settings: UISettings | None = None) -> Path | None:
    ui_settings = settings or UISettings()
    return ui_settings.sample_image_path if ui_settings.sample_image_path.exists() else None



def run_food_inference(
    image_path: str | Path,
    options: UIInferenceOptions,
    recipe_data_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    resolved_image_path = Path(image_path)
    if not resolved_image_path.exists():
        raise FileNotFoundError(f"Image not found for UI inference: {resolved_image_path}")

    settings = UISettings()
    preflight_warnings: list[str] = []
    runtime_diagnostics = get_runtime_diagnostics(settings.expected_env_name)
    if options.enable_segmentation:
        preflight_warnings.append("segmentation_requested_but_no_adapter_configured")
    if not runtime_diagnostics.get("expected_env_match"):
        preflight_warnings.append("unexpected_runtime_environment")

    prediction_result = predict_dish(
        image_path=resolved_image_path,
        top_k=options.top_k_dishes,
        backend="pytorch",
    )
    dish_rows = [
        {
            "label": prediction.label,
            "name": prediction.name,
            "score": prediction.confidence,
            "confidence": prediction.confidence,
            "source": prediction_result.backend,
        }
        for prediction in prediction_result.predictions
    ]
    resolved_db_path = resolve_runtime_db_path(db_path)
    bridge = build_food_bridge(
        dish_rows,
        db_path=resolved_db_path,
        top_k_dishes=options.top_k_dishes,
        top_k_ingredients=options.top_k_ingredients,
        use_dish_mass_priors=options.use_dish_mass_priors,
        use_nutrition5k=options.use_nutrition5k,
    )
    result: dict[str, Any] = {
        "image_path": prediction_result.image_path,
        "dish_classifier_topk": dish_rows,
        "dish_fused_topk": dish_rows,
        "confidence": {"overall": dish_rows[0]["confidence"] if dish_rows else None},
        "ingredients_ranked": bridge.ingredients_ranked,
        "visible_ingredients": bridge.visible_ingredients,
        "ingredient_normalization": bridge.ingredient_normalization,
        "warnings": sorted(set(preflight_warnings + bridge.warnings)),
        "runtime_diagnostics": runtime_diagnostics,
        "usda_portion_audit": audit_usda_portion_tables(resolved_db_path),
        "palm_ready": bridge.palm_ready,
        "metadata": {
            "inference_mode": "classifier_only",
            "backend": prediction_result.backend,
            "food_bridge_status": bridge.status,
            "use_dish_mass_priors": options.use_dish_mass_priors,
            "use_nutrition5k": options.use_nutrition5k,
            "actual_weight_source": bridge.palm_ready.get("actual_weight_source"),
            "nutrition5k_matched_dish_count": bridge.palm_ready.get("nutrition5k_matched_dish_count"),
            "model_path": prediction_result.model_path,
            "recipe_data_path_ignored": str(recipe_data_path) if recipe_data_path is not None else None,
            "db_path": str(resolved_db_path),
        },
    }
    if preflight_warnings:
        result["warnings"] = sorted(set((result.get("warnings") or []) + preflight_warnings))
    logger.info(
        "UI inference completed for %s with backend=%s",
        resolved_image_path,
        prediction_result.backend,
    )
    return result
