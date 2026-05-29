from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import sys
from pathlib import Path


sys.dont_write_bytecode = True
_PACKAGE_ROOT = Path(__file__).resolve().parent


def _find_pyc(base_dir: Path, stem: str) -> Path | None:
    pycache_dir = base_dir / "__pycache__"
    if not pycache_dir.exists():
        return None
    exact_name = f"{stem}.cpython-{sys.version_info.major}{sys.version_info.minor}.pyc"
    exact_path = pycache_dir / exact_name
    if exact_path.exists():
        return exact_path
    matches = sorted(pycache_dir.glob(f"{stem}.cpython-*.pyc"))
    return matches[0] if matches else None


class _FoodAIPycFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: object = None, target: object = None):
        if not fullname.startswith("food_ai."):
            return None

        relative_parts = fullname.split(".")[1:]
        base_candidate = _PACKAGE_ROOT.joinpath(*relative_parts)

        if base_candidate.is_dir():
            source_init = base_candidate / "__init__.py"
            if source_init.exists():
                return None
            pyc_path = _find_pyc(base_candidate, "__init__")
            if pyc_path is None:
                return None
            loader = importlib.machinery.SourcelessFileLoader(fullname, str(pyc_path))
            return importlib.util.spec_from_file_location(
                fullname,
                pyc_path,
                loader=loader,
                submodule_search_locations=[str(base_candidate)],
            )

        module_dir = base_candidate.parent
        module_name = base_candidate.name
        source_file = module_dir / f"{module_name}.py"
        if source_file.exists():
            return None
        pyc_path = _find_pyc(module_dir, module_name)
        if pyc_path is None:
            return None
        loader = importlib.machinery.SourcelessFileLoader(fullname, str(pyc_path))
        return importlib.util.spec_from_file_location(fullname, pyc_path, loader=loader)


if not any(isinstance(finder, _FoodAIPycFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _FoodAIPycFinder())


_original_init_pyc = _find_pyc(_PACKAGE_ROOT, "__init__")
if _original_init_pyc is not None:
    loader = importlib.machinery.SourcelessFileLoader("food_ai._pyc_init", str(_original_init_pyc))
    spec = importlib.util.spec_from_file_location("food_ai._pyc_init", _original_init_pyc, loader=loader)
    if spec is not None:
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        exported = getattr(module, "__all__", None)
        if exported:
            for name in exported:
                if hasattr(module, name):
                    globals()[name] = getattr(module, name)
            __all__ = list(exported)
