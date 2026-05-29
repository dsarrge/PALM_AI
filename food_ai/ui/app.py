from __future__ import annotations

from pathlib import Path
import logging
import sys

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from food_ai.ui.components.debug_panel import render_debug_panel
from food_ai.ui.components.dish_panel import render_dish_panel
from food_ai.ui.components.ingredients_panel import render_ingredients_panel
from food_ai.ui.components.logging_panel import render_logging_panel
from food_ai.ui.components.nutrition_panel import render_nutrition_panel
from food_ai.ui.components.results_summary import render_results_summary
from food_ai.ui.components.status_panel import render_status_panel
from food_ai.ui.components.upload_panel import render_upload_panel, render_image_preview

from food_ai.food_bridge.nutrition_bridge import recalculate_bridge_nutrition
from food_ai.ui.config import UISettings

from food_ai.ui.services.formatters import format_result_for_ui
from food_ai.ui.services.inference_service import UIInferenceOptions, run_food_inference
from food_ai.ui.services.log_service import save_test_log
from food_ai.ui.services.runtime_service import format_runtime_status_for_ui, get_runtime_diagnostics

from food_ai.ui.utils.file_io import ensure_directory, file_digest, save_uploaded_file
from food_ai.ui.utils.session import clear_inference_state, ensure_session_state

logger = logging.getLogger(__name__)


def _sidebar_controls(settings: UISettings) -> tuple[UIInferenceOptions, Path, Path, bool]:
    st.sidebar.header("Tester Controls")
    artifact_root = Path(
        st.sidebar.text_input(
            "Output Folder for Test Artifacts",
            value=str(settings.logs_dir.parent),
            help="Uploads, optional test logs, and optional run reports will be written under this local folder.",
        ))
    top_k_dishes = st.sidebar.slider("Top-K Dishes", min_value=3, max_value=10, value=5)
    auto_save_logs = st.sidebar.checkbox("Auto-save Test Log After Inference", value=False)
    show_debug = st.sidebar.checkbox("Show Debug Details", value=False)

    options = UIInferenceOptions(
        top_k_dishes=top_k_dishes,
        top_k_recipes=8,
        top_k_ingredients=20,
        show_debug=show_debug,
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
    return options, artifact_root, artifact_root / "test_logs", auto_save_logs


def _handle_image_selection(settings: UISettings, artifacts_root: Path) -> None:
    del settings

    upload_state = render_upload_panel()
    uploads_dir = ensure_directory(artifacts_root / "uploads")

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

    clear_inference_state(st.session_state)
    st.session_state["status_message"] = f"Loaded image: {uploaded_file.name}"
    

def _run_inference(options: UIInferenceOptions, logs_dir: Path, auto_save_logs: bool) -> None:
    if not st.session_state.get("uploaded_image_path"):
        st.warning("Upload an image before running inference.")
        return

    result = run_food_inference(
        image_path=st.session_state["uploaded_image_path"],
        options=options,
    )
    formatted = format_result_for_ui(result)
    st.session_state["inference_result"] = result
    st.session_state["base_inference_result"] = result
    st.session_state["nutrition_recalc_result"] = None
    st.session_state["formatted_result"] = formatted
    st.session_state["excluded_ingredients"] = []
    st.session_state["last_inference_id"] = st.session_state.get("uploaded_image_digest") or options.fingerprint()
    st.session_state["last_options_fingerprint"] = options.fingerprint()
    st.session_state["last_error"] = None

    if auto_save_logs:
        log_path = save_test_log(
            image_path=st.session_state["uploaded_image_path"],
            inference_result=result,
            notes=st.session_state.get("tester_notes", ""),
            review_status=st.session_state.get("review_status", "not_reviewed"),
            logs_dir=logs_dir,
        )
        st.session_state["status_message"] = f"Inference completed and test log saved to {log_path.name}"
    else:
        st.session_state["status_message"] = "Inference completed."


def _recalculate_nutrition() -> bool:
    base_result = st.session_state.get("base_inference_result") or st.session_state.get("inference_result")
    if not base_result:
        st.warning("Run inference before recalculating nutrition.")
        return False

    excluded = st.session_state.get("excluded_ingredients") or []
    ranked = (st.session_state.get("formatted_result") or {}).get("ingredients", {}).get("ranked") or []
    included_count = len([row for row in ranked if str(row.get("normalized_ingredient") or row.get("ingredient") or "").strip().lower() not in set(excluded)])
    if included_count <= 0:
        st.warning("Select at least one ingredient to calculate nutrition.")
        return False

    recalculated = recalculate_bridge_nutrition(base_result, excluded)
    formatted = format_result_for_ui(recalculated)
    st.session_state["nutrition_recalc_result"] = recalculated
    st.session_state["inference_result"] = recalculated
    st.session_state["formatted_result"] = formatted
    st.session_state["status_message"] = "Nutrition recalculated from selected ingredients."
    return True


def main() -> None:
    st.set_page_config(page_title="PALM Food Image Classifier", layout="wide")
    settings = UISettings()
    ensure_session_state(st.session_state)
    runtime_diagnostics = get_runtime_diagnostics(settings.expected_env_name)
    runtime_status = format_runtime_status_for_ui(runtime_diagnostics)

    st.title(settings.app_title)
    st.caption(settings.app_description)

    if not runtime_status.get("expected_env_match"):
        st.warning(
            f"App is not running from the expected environment '{settings.expected_env_name}'. "
            f"Current env: {runtime_status.get('conda_env') or 'unknown'} | Executable: {runtime_status.get('sys_executable')}"
        )
    elif runtime_status.get("degraded"):
        st.warning(
            "Runtime is degraded. The app is running, but part of the ML stack is unavailable. "
            f"Warnings: {', '.join(runtime_status.get('warnings') or [])}"
        )
    else:
        st.info(
            f"Runtime healthy: env={runtime_status.get('conda_env')} | Python {runtime_status.get('python_version')} | "
            f"CUDA={runtime_status.get('cuda_available')}"
        )

    options, artifacts_root, logs_dir, auto_save_logs = _sidebar_controls(settings)
    _handle_image_selection(settings, artifacts_root)

    status_message = st.session_state.get("status_message")
    if status_message:
        st.success(status_message)
        st.session_state["status_message"] = None

    render_image_preview(st.session_state.get("uploaded_image_path"))

    if st.button("Run Inference", width="stretch", disabled=not bool(st.session_state.get("uploaded_image_path"))):
        try:
            _run_inference(options, logs_dir, auto_save_logs)
            st.rerun()
        except Exception as exc:  # pragma: no cover - Streamlit-only path
            logger.exception("UI inference failed")
            st.session_state["last_error"] = str(exc)

    if st.session_state.get("last_error"):
        st.error(f"Inference error: {st.session_state['last_error']}")

    formatted = st.session_state.get("formatted_result")
    if formatted:
        render_status_panel(formatted)
        render_results_summary(formatted)
        render_dish_panel(formatted)
        render_ingredients_panel(formatted)
        if st.button("Recalculate Nutrition", width="stretch"):
            if _recalculate_nutrition():
                st.rerun()
        render_nutrition_panel(formatted)
        render_debug_panel(formatted, show_debug=options.show_debug)

        logging_state = render_logging_panel(
            notes=st.session_state.get("tester_notes", ""),
            review_status=st.session_state.get("review_status", "not_reviewed"),
        )
        st.session_state["tester_notes"] = logging_state["notes"]
        st.session_state["review_status"] = logging_state["review_status"]

        if logging_state["save_clicked"]:
            try:
                log_path = save_test_log(
                    image_path=st.session_state["uploaded_image_path"],
                    inference_result=st.session_state["inference_result"],
                    notes=st.session_state["tester_notes"],
                    review_status=st.session_state["review_status"],
                    logs_dir=logs_dir,
                )
                st.success(f"Saved test log: {log_path}")
            except Exception as exc:  # pragma: no cover - Streamlit-only path
                logger.exception("Saving UI test log failed")
                st.error(f"Could not save test log: {exc}")
    else:
        st.info("Run inference to inspect dish evidence, ingredient predictions, USDA bridge output, warnings, missing artifacts, fallback paths, and raw debug JSON.")

    if options.developer_mode:
        with st.sidebar.expander("Developer Context", expanded=False):
            st.write(f"Uploaded image: {st.session_state.get('uploaded_image_path')}")
            st.write(f"Loaded sample case: {st.session_state.get('loaded_sample_case')}")
            st.write(f"Last options fingerprint: {st.session_state.get('last_options_fingerprint')}")
            st.write(f"Artifacts root: {artifacts_root}")
            st.write(f"Logs dir: {logs_dir}")
            st.json(runtime_status)


if __name__ == "__main__":
    main()


