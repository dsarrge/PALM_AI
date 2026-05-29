from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4


def ensure_directory(path: str | Path) -> Path:
    resolved_path = Path(path)
    resolved_path.mkdir(parents=True, exist_ok=True)
    return resolved_path


def file_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_timestamped_name(filename: str) -> str:
    suffix = Path(filename).suffix or ".jpg"
    stem = Path(filename).stem or "image"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stem}_{timestamp}_{uuid4().hex[:8]}{suffix}"


def save_uploaded_file(uploaded_file: Any, output_dir: str | Path) -> tuple[Path, str]:
    target_dir = ensure_directory(output_dir)
    content = uploaded_file.getvalue()
    digest = file_digest(content)
    target_path = target_dir / build_timestamped_name(uploaded_file.name)
    target_path.write_bytes(content)
    return target_path, digest


def copy_local_image(source_path: str | Path, output_dir: str | Path) -> Path:
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Local image not found: {source}")
    target_dir = ensure_directory(output_dir)
    target_path = target_dir / build_timestamped_name(source.name)
    shutil.copy2(source, target_path)
    return target_path


def write_json_file(path: str | Path, payload: dict[str, Any]) -> Path:
    resolved_path = Path(path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = resolved_path.with_suffix(resolved_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    temp_path.replace(resolved_path)
    return resolved_path
