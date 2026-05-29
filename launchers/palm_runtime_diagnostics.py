from __future__ import annotations

import importlib
import sys
from typing import Any


def _module_status(module_name: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        return {"available": False, "version": None, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "version": getattr(module, "__version__", None),
        "error": None,
    }


def _torch_status() -> dict[str, Any]:
    status = _module_status("torch")
    if not status["available"]:
        status.update({"cuda_available": False, "gpu_name": None})
        return status

    import torch

    cuda_available = bool(torch.cuda.is_available())
    gpu_name = torch.cuda.get_device_name(0) if cuda_available else None
    status.update({"cuda_available": cuda_available, "gpu_name": gpu_name})
    return status


def main() -> int:
    torch_status = _torch_status()
    sentence_transformers_status = _module_status("sentence_transformers")
    transformers_status = _module_status("transformers")
    streamlit_status = _module_status("streamlit")

    print("PALM Runtime Diagnostics")
    print("=" * 32)
    print(f"Interpreter: {sys.executable}")
    print(f"Python: {sys.version}")
    print("")
    print(f"torch available: {torch_status['available']}")
    print(f"torch version: {torch_status.get('version')}")
    print(f"CUDA available: {torch_status.get('cuda_available')}")
    print(f"GPU name: {torch_status.get('gpu_name')}")
    if torch_status.get("error"):
        print(f"torch error: {torch_status['error']}")
    print("")
    print(f"sentence_transformers available: {sentence_transformers_status['available']}")
    print(f"sentence_transformers version: {sentence_transformers_status.get('version')}")
    if sentence_transformers_status.get("error"):
        print(f"sentence_transformers error: {sentence_transformers_status['error']}")
    print("")
    print(f"transformers available: {transformers_status['available']}")
    print(f"transformers version: {transformers_status.get('version')}")
    if transformers_status.get("error"):
        print(f"transformers error: {transformers_status['error']}")
    print("")
    print(f"streamlit available: {streamlit_status['available']}")
    print(f"streamlit version: {streamlit_status.get('version')}")
    if streamlit_status.get("error"):
        print(f"streamlit error: {streamlit_status['error']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
