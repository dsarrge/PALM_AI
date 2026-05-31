from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging
import sys
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from food_ai.barcode.image_scan import scan_barcode_image
from food_ai.config import resolve_runtime_db_path
from food_ai.food_bridge.ingredient_normalize import normalize_ingredient
from food_ai.food_bridge.nutrition_bridge import recalculate_bridge_nutrition
from food_ai.ui.components.ingredients_panel import render_ingredients_panel
from food_ai.ui.components.meal_log_panel import render_meal_log_form, render_recent_meal_logs
from food_ai.ui.components.nutrition_panel import render_nutrition_panel
from food_ai.ui.components.results_summary import render_results_summary
from food_ai.ui.components.status_panel import render_status_panel
from food_ai.ui.components.upload_panel import render_image_preview, render_upload_panel
from food_ai.ui.config import UISettings
from food_ai.ui.services.barcode_meal_log_service import lookup_barcode_product, save_barcode_meal_log
from food_ai.ui.services.formatters import format_result_for_ui
from food_ai.ui.services.inference_service import UIInferenceOptions, run_food_inference
from food_ai.ui.services.ingredient_weight_guardrail_service import (
    DEFAULT_INGREDIENT_WEIGHT_POLICY,
    SUPPORTED_INGREDIENT_WEIGHT_POLICIES,
    apply_ingredient_weight_guardrails,
)
from food_ai.ui.services.manual_ingredient_service import (
    apply_manual_ingredients_to_bridge_result,
    manual_exclusion_keys,
)
from food_ai.ui.services.meal_log_service import list_recent_meal_logs, save_meal_log
from food_ai.ui.services.runtime_service import format_runtime_status_for_ui, get_runtime_diagnostics
from food_ai.ui.utils.file_io import ensure_directory, file_digest, save_uploaded_file
from food_ai.ui.utils.session import clear_inference_state, ensure_session_state

logger = logging.getLogger(__name__)

_WEIGHT_POLICY_LABELS = {
    "mode": "Mode - most common weight",
    "mean": "Mean - previous default",
    "guardrail": "Guardrail - mode only for volatile rows",
}


def _sidebar_controls(settings: UISettings) -> tuple[UIInferenceOptions, Path, Path, str]:
    st.sidebar.header("Production Settings")
    artifact_root = Path(
        st.sidebar.text_input(
            "Local artifact folder",
            value=str(settings.logs_dir.parent),
            help="Uploaded meal photos and run reports are stored locally. Meal logs are written to the PALM SQLite database.",
        )
    )
    db_path = Path(
        st.sidebar.text_input(
            "Meal log database",
            value=str(resolve_runtime_db_path()),
            help="Defaults to shared/PALM_V2.db when present, otherwise shared/palm.db.",
        )
    )
    top_k_dishes = st.sidebar.slider("Dish candidates", min_value=3, max_value=10, value=5)
    weight_policy = st.sidebar.radio(
        "Ingredient weight default",
        options=list(SUPPORTED_INGREDIENT_WEIGHT_POLICIES),
        index=list(SUPPORTED_INGREDIENT_WEIGHT_POLICIES).index(DEFAULT_INGREDIENT_WEIGHT_POLICY),
        format_func=lambda value: _WEIGHT_POLICY_LABELS.get(value, value),
        help="Mode is now the default for testing. Switch to Mean to compare against the previous behavior.",
    )
    show_pipeline_status = st.sidebar.checkbox("Show pipeline status", value=False)

    options = UIInferenceOptions(
        top_k_dishes=top_k_dishes,
        top_k_recipes=8,
        top_k_ingredients=20,
        show_debug=False,
        enable_segmentation=False,
        prefer_calibrated_probabilities=True,
        enable_embedding_cache=True,
        save_run_report=True,
        run_report_dir=artifact_root / "run_reports",
        use_dish_mass_priors=True,
        use_nutrition5k=False,
        local_files_only=True,
        developer_mode=False,
    )
    st.session_state["prod_db_path"] = str(db_path)
    st.session_state["show_pipeline_status"] = show_pipeline_status
    st.session_state["ingredient_weight_policy"] = weight_policy
    return options, artifact_root, db_path, weight_policy


def _ingredient_key(row_or_text: dict[str, Any] | str) -> str:
    if isinstance(row_or_text, str):
        value = row_or_text
    else:
        value = (
            row_or_text.get("normalized")
            or row_or_text.get("normalized_ingredient")
            or row_or_text.get("matched_ingredient")
            or row_or_text.get("canonical")
            or row_or_text.get("raw_ingredient")
            or row_or_text.get("ingredient")
            or ""
        )
    return normalize_ingredient(str(value)) or str(value).strip().lower()


def _already_excluded_ingredient_keys(result: dict[str, Any]) -> list[str]:
    excluded: list[str] = []
    row_groups: list[Any] = [
        result.get("ingredients_ranked") or [],
        result.get("visible_ingredients") or [],
    ]
    palm_ready = result.get("palm_ready") or {}
    row_groups.extend(
        [
            palm_ready.get("matched_ingredients") or [],
            palm_ready.get("unmatched_ingredients") or [],
        ]
    )
    for rows in row_groups:
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("included_in_nutrition") is False or row.get("include_in_nutrition") is False:
                key = _ingredient_key(row)
                if key:
                    excluded.append(key)
    return list(dict.fromkeys(excluded))


def _combined_exclusions(result: dict[str, Any], user_exclusions: list[str] | None = None) -> list[str]:
    return list(dict.fromkeys(_already_excluded_ingredient_keys(result) + list(user_exclusions or [])))


def _row_is_included(row: dict[str, Any], excluded: set[str]) -> bool:
    if row.get("included_in_nutrition") is False or row.get("include_in_nutrition") is False:
        return False
    key = _ingredient_key(row)
    return not (key and key in excluded)


def _handle_image_selection(artifacts_root: Path) -> None:
    upload_state = render_upload_panel()
    uploads_dir = ensure_directory(artifacts_root / "meal_uploads")
    uploaded_file = upload_state["uploaded_file"]
    if uploaded_file is None:
        return

    digest = file_digest(uploaded_file.getvalue())
    if digest == st.session_state.get("uploaded_image_digest"):
        return

    saved_path, saved_digest = save_uploaded_file(uploaded_file, uploads_dir)
    st.session_state["uploaded_image_path"] = str(saved_path)
    st.session_state["uploaded_image_name"] = uploaded_file.name
    st.session_state["uploaded_image_digest"] = saved_digest
    st.session_state["loaded_sample_case"] = None
    st.session_state["manual_ingredients"] = []
    clear_inference_state(st.session_state)
    st.session_state["status_message"] = f"Loaded image: {uploaded_file.name}"


def _run_inference(options: UIInferenceOptions, db_path: Path, weight_policy: str) -> None:
    if not st.session_state.get("uploaded_image_path"):
        st.warning("Upload a meal image before analyzing.")
        return

    raw_result = run_food_inference(
        image_path=st.session_state["uploaded_image_path"],
        options=options,
        db_path=db_path,
    )
    st.session_state["raw_inference_result"] = raw_result
    guarded_result = apply_ingredient_weight_guardrails(raw_result, policy=weight_policy)
    excluded = _combined_exclusions(guarded_result)
    recalculated = recalculate_bridge_nutrition(guarded_result, excluded)
    formatted = format_result_for_ui(recalculated)
    st.session_state["inference_result"] = recalculated
    st.session_state["base_inference_result"] = recalculated
    st.session_state["nutrition_recalc_result"] = None
    st.session_state["formatted_result"] = formatted
    st.session_state["excluded_ingredients"] = excluded
    st.session_state["manual_ingredients"] = []
    st.session_state["last_inference_id"] = st.session_state.get("uploaded_image_digest") or options.fingerprint()
    st.session_state["last_options_fingerprint"] = options.fingerprint()
    st.session_state["last_error"] = None
    st.session_state["status_message"] = f"Meal analyzed with ingredient weight policy: {weight_policy}."


def _selected_exclusion_keys() -> list[str]:
    return list(dict.fromkeys((st.session_state.get("excluded_ingredients") or []) + manual_exclusion_keys(st.session_state.get("manual_ingredients") or [])))


def _result_with_manual_ingredients(db_path: Path) -> dict:
    base_result = st.session_state.get("raw_inference_result") or st.session_state.get("base_inference_result") or st.session_state.get("inference_result")
    if not base_result:
        raise ValueError("Analyze a meal before applying manual ingredients.")
    return apply_manual_ingredients_to_bridge_result(
        base_result,
        st.session_state.get("manual_ingredients") or [],
        db_path=db_path,
    )


def _recalculate_nutrition(db_path: Path, weight_policy: str) -> bool:
    if not (st.session_state.get("base_inference_result") or st.session_state.get("inference_result")):
        st.warning("Analyze a meal before recalculating nutrition.")
        return False

    merged_result = _result_with_manual_ingredients(db_path)
    guarded_result = apply_ingredient_weight_guardrails(merged_result, policy=weight_policy)
    excluded = _combined_exclusions(guarded_result, _selected_exclusion_keys())
    ranked = guarded_result.get("ingredients_ranked") or []
    included_count = len([row for row in ranked if isinstance(row, dict) and _row_is_included(row, set(excluded))])
    if included_count <= 0:
        st.warning("Select at least one ingredient to calculate nutrition.")
        return False

    recalculated = recalculate_bridge_nutrition(guarded_result, excluded)
    formatted = format_result_for_ui(recalculated)
    st.session_state["nutrition_recalc_result"] = recalculated
    st.session_state["inference_result"] = recalculated
    st.session_state["formatted_result"] = formatted
    st.session_state["excluded_ingredients"] = excluded
    st.session_state["status_message"] = f"Nutrition recalculated with ingredient weight policy: {weight_policy}."
    return True


def _save_current_meal(form_state: dict[str, object], db_path: Path, weight_policy: str) -> None:
    if not st.session_state.get("formatted_result") or not st.session_state.get("inference_result"):
        st.warning("Analyze a meal before saving.")
        return

    if _recalculate_nutrition(db_path, weight_policy):
        formatted = st.session_state["formatted_result"]
        inference_result = st.session_state["inference_result"]
    else:
        formatted = st.session_state.get("formatted_result")
        inference_result = st.session_state.get("inference_result")

    record = save_meal_log(
        image_path=st.session_state.get("uploaded_image_path"),
        inference_result=inference_result,
        formatted_result=formatted,
        serving_weight_g=float(form_state["serving_weight_g"]),
        notes=str(form_state.get("notes") or ""),
        user_label=str(form_state.get("user_label") or ""),
        eaten_at=str(form_state.get("eaten_at") or ""),
        excluded_ingredients=_selected_exclusion_keys(),
        db_path=db_path,
    )
    st.session_state["status_message"] = (
        f"Saved meal log: {record.dish_name} | {record.serving_weight_g:g}g | "
        f"{record.calories if record.calories is not None else 'n/a'} calories"
    )


def _render_barcode_panel(db_path: Path) -> None:
    st.subheader("Barcode Photo Scan")
    barcode_file = st.file_uploader(
        "Upload barcode photo",
        type=["jpg", "jpeg", "png", "webp"],
        key="barcode_photo_upload",
        help="Crop tightly around the UPC/EAN barcode for best results.",
    )
    manual_barcode = st.text_input("Or enter barcode manually", key="barcode_manual_input")

    if st.button("Scan / Lookup Barcode", width="stretch", key="scan_barcode_button"):
        barcode = "".join(ch for ch in str(manual_barcode or "") if ch.isdigit())
        scan_result: dict[str, Any] | None = None
        if not barcode and barcode_file is not None:
            scan_result = scan_barcode_image(barcode_file.getvalue())
            barcode = str(scan_result.get("barcode") or "")
        if scan_result is not None:
            st.session_state["barcode_scan_result"] = scan_result
        if not barcode:
            st.warning("No barcode found. Upload a clearer barcode photo or enter the digits manually.")
            return
        lookup = lookup_barcode_product(barcode, db_path=db_path)
        st.session_state["barcode_lookup_result"] = lookup

    scan_result = st.session_state.get("barcode_scan_result")
    if scan_result:
        st.caption(f"Scan status: {scan_result.get('status')} | decoder: {scan_result.get('decoder') or 'n/a'}")
        if scan_result.get("message"):
            st.info(str(scan_result["message"]))

    lookup = st.session_state.get("barcode_lookup_result")
    if not lookup:
        return
    if lookup.get("status") == "unknown_barcode":
        st.warning(f"Barcode not found locally. Saved to unknown_barcodes: {lookup.get('barcode')}")
        return
    if lookup.get("status") != "found":
        st.warning(f"Barcode lookup status: {lookup.get('status')}")
        return

    product = lookup.get("product") or {}
    st.success(f"Found: {product.get('brand') or ''} {product.get('product_name') or ''}".strip())
    serving_default = float(product.get("serving_size_default_g") or 100.0)
    grams = st.number_input(
        "Grams consumed",
        min_value=1.0,
        max_value=5000.0,
        value=max(1.0, serving_default),
        step=1.0,
        key="barcode_grams_consumed",
    )
    st.json({"barcode": lookup.get("barcode"), "product": product})
    if st.button("Save Barcode Food Log", width="stretch", key="save_barcode_food_log_button"):
        saved = save_barcode_meal_log(
            barcode=str(lookup.get("barcode") or ""),
            grams_consumed=float(grams),
            db_path=db_path,
        )
        if saved.get("status") == "saved":
            st.success(f"Saved barcode food log: {saved.get('grams_consumed'):g}g")
            st.session_state["barcode_lookup_result"] = None
        else:
            st.warning(f"Could not save barcode food log: {saved}")


def main() -> None:
    st.set_page_config(page_title="PALM Meal Logger", layout="wide")
    settings = UISettings()
    ensure_session_state(st.session_state)

    runtime_diagnostics = get_runtime_diagnostics(settings.expected_env_name)
    runtime_status = format_runtime_status_for_ui(runtime_diagnostics)

    st.title("PALM Meal Logger")
    st.caption("Production Streamlit UI for analyzing meal photos and saving reviewed meal logs to the local PALM database.")

    if runtime_status.get("degraded"):
        st.warning("Runtime is degraded. Meal logging still works, but some inference components may be unavailable.")

    options, artifacts_root, db_path, weight_policy = _sidebar_controls(settings)
    _handle_image_selection(artifacts_root)

    status_message = st.session_state.get("status_message")
    if status_message:
        st.success(status_message)
        st.session_state["status_message"] = None

    input_col, results_col = st.columns([1, 2], gap="large")
    with input_col:
        render_image_preview(st.session_state.get("uploaded_image_path"), max_width=340)
        analyze_disabled = not bool(st.session_state.get("uploaded_image_path"))
        if st.button("Analyze Meal", width="stretch", disabled=analyze_disabled):
            try:
                _run_inference(options, db_path, weight_policy)
                st.rerun()
            except Exception as exc:  # pragma: no cover - Streamlit-only path
                logger.exception("Production UI inference failed")
                st.session_state["last_error"] = str(exc)

        if st.session_state.get("last_error"):
            st.error(f"Analysis error: {st.session_state['last_error']}")

        with st.expander("Packaged food barcode", expanded=False):
            _render_barcode_panel(db_path)

        recent_rows = list_recent_meal_logs(db_path=db_path, limit=10)
        render_recent_meal_logs(recent_rows)

    with results_col:
        formatted = st.session_state.get("formatted_result")
        if not formatted:
            st.info("Upload a meal photo, run analysis, then review and save the meal log.")
            return

        render_results_summary(formatted)
        if st.session_state.get("show_pipeline_status"):
            render_status_panel(formatted)

        render_ingredients_panel(formatted, allow_manual_ingredients=True, db_path=db_path)
        if st.button("Recalculate Nutrition From Selected Ingredients", width="stretch"):
            try:
                if _recalculate_nutrition(db_path, weight_policy):
                    st.rerun()
            except Exception as exc:  # pragma: no cover - Streamlit-only path
                logger.exception("Production UI nutrition recalculation failed")
                st.error(f"Could not recalculate nutrition: {exc}")

        render_nutrition_panel(formatted)

        default_name = (formatted.get("summary") or {}).get("top_dish") or ""
        form_state = render_meal_log_form(default_name=default_name)
        if form_state["save_clicked"]:
            try:
                _save_current_meal(form_state, db_path, weight_policy)
                st.rerun()
            except Exception as exc:  # pragma: no cover - Streamlit-only path
                logger.exception("Saving meal log failed")
                st.error(f"Could not save meal log: {exc}")

    with st.sidebar.expander("Runtime", expanded=False):
        st.write(f"Database: {db_path}")
        st.write(f"Artifacts: {artifacts_root}")
        st.write(f"Ingredient weight policy: {weight_policy}")
        st.write(f"Generated at: {datetime.now().isoformat(timespec='seconds')}")
        st.json(runtime_status)


if __name__ == "__main__":
    main()
