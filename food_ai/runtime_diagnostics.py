from __future__ import annotations

import logging
import os
import sys
from typing import Any


logger = logging.getLogger(__name__)


def _detect_torch() -> dict[str, Any]:
    status: dict[str, Any] = {
        "available": False,
        "cuda_available": False,
        "device_name": None,
        "version": None,
        "error": None,
    }
    try:
        import torch
    except Exception as exc:  # pragma: no cover - import environment specific
        status["error"] = f"{type(exc).__name__}: {exc}"
        return status

    status["available"] = True
    status["version"] = getattr(torch, "__version__", None)
    try:
        status["cuda_available"] = bool(torch.cuda.is_available())
    except Exception as exc:  # pragma: no cover - runtime environment specific
        status["error"] = f"{type(exc).__name__}: {exc}"
        return status
    if status["cuda_available"]:
        try:
            status["device_name"] = torch.cuda.get_device_name(0)
        except Exception as exc:  # pragma: no cover - runtime environment specific
            status["error"] = f"{type(exc).__name__}: {exc}"
    return status



def _detect_import(module_name: str) -> dict[str, Any]:
    status: dict[str, Any] = {"available": False, "version": None, "error": None}
    try:
        module = __import__(module_name)
    except Exception as exc:  # pragma: no cover - import environment specific
        status["error"] = f"{type(exc).__name__}: {exc}"
        return status
    status["available"] = True
    status["version"] = getattr(module, "__version__", None)
    return status



def get_runtime_diagnostics(expected_env_name: str | None = None) -> dict[str, Any]:
    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    executable = sys.executable
    python_version = sys.version.split()[0]

    expected_match = expected_env_name in {None, "", conda_env}
    if expected_env_name and not conda_env and expected_env_name.lower() in executable.lower():
        expected_match = True

    torch_status = _detect_torch()
    sentence_transformers_status = _detect_import("sentence_transformers")
    transformers_status = _detect_import("transformers")

    warnings: list[str] = []
    if expected_env_name and not expected_match:
        warnings.append("unexpected_runtime_environment")
    if not torch_status["available"]:
        warnings.append("torch_unavailable")
    if torch_status["available"] and not torch_status["cuda_available"]:
        warnings.append("cuda_unavailable")
    if not sentence_transformers_status["available"]:
        warnings.append("sentence_transformers_unavailable")
    if not transformers_status["available"]:
        warnings.append("transformers_unavailable")

    diagnostics = {
        "python_version": python_version,
        "sys_executable": executable,
        "conda_env": conda_env,
        "expected_env_name": expected_env_name,
        "expected_env_match": expected_match,
        "torch": torch_status,
        "sentence_transformers": sentence_transformers_status,
        "transformers": transformers_status,
        "warnings": warnings,
        "degraded": bool(warnings),
    }
    logger.info(
        "Runtime diagnostics: env=%s expected=%s match=%s torch=%s cuda=%s st=%s transformers=%s",
        conda_env,
        expected_env_name,
        expected_match,
        torch_status["available"],
        torch_status["cuda_available"],
        sentence_transformers_status["available"],
        transformers_status["available"],
    )
    return diagnostics
