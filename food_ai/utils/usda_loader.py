from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_usda_csv(path: str | Path) -> pd.DataFrame:
    csv_path = Path(path)
    if not csv_path.exists():
        return pd.DataFrame()
    return pd.read_csv(csv_path)
