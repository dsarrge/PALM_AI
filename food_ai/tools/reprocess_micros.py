from __future__ import annotations

from pathlib import Path
from typing import Any


def reprocess_micros(db_path: str | Path) -> dict[str, Any]:
    return {'status': 'skipped', 'db_path': str(db_path)}
