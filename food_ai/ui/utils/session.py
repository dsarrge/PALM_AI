from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from food_ai.ui.state import DEFAULT_SESSION_STATE


def ensure_session_state(session_state: MutableMapping[str, Any]) -> None:
    for key, value in DEFAULT_SESSION_STATE.items():
        session_state.setdefault(key, value)


def clear_inference_state(session_state: MutableMapping[str, Any]) -> None:
    session_state["inference_result"] = None
    session_state["formatted_result"] = None
    session_state["base_inference_result"] = None
    session_state["nutrition_recalc_result"] = None
    session_state["excluded_ingredients"] = []
    session_state["last_inference_id"] = None
    session_state["last_error"] = None
    session_state["status_message"] = None
