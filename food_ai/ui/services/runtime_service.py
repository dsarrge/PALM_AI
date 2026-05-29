from __future__ import annotations

from typing import Any

from food_ai.runtime_diagnostics import get_runtime_diagnostics as collect_runtime_diagnostics



def get_runtime_diagnostics(expected_env_name: str | None = None) -> dict[str, Any]:
    return collect_runtime_diagnostics(expected_env_name=expected_env_name)



def format_runtime_status_for_ui(diagnostics: dict[str, Any]) -> dict[str, Any]:
    torch_status = diagnostics.get("torch") or {}
    sentence_transformers_status = diagnostics.get("sentence_transformers") or {}
    transformers_status = diagnostics.get("transformers") or {}
    return {
        "python_version": diagnostics.get("python_version"),
        "sys_executable": diagnostics.get("sys_executable"),
        "conda_env": diagnostics.get("conda_env"),
        "expected_env_name": diagnostics.get("expected_env_name"),
        "expected_env_match": diagnostics.get("expected_env_match"),
        "torch_available": torch_status.get("available"),
        "torch_version": torch_status.get("version"),
        "cuda_available": torch_status.get("cuda_available"),
        "gpu_device_name": torch_status.get("device_name"),
        "sentence_transformers_available": sentence_transformers_status.get("available"),
        "sentence_transformers_version": sentence_transformers_status.get("version"),
        "transformers_available": transformers_status.get("available"),
        "transformers_version": transformers_status.get("version"),
        "warnings": diagnostics.get("warnings") or [],
        "degraded": bool(diagnostics.get("degraded")),
    }
