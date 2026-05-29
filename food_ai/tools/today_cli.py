from __future__ import annotations

import json
from pathlib import Path

from food_ai.config import DB_PATH
from food_ai.core.daily_totals import get_daily_totals


def main() -> None:
    print(json.dumps(get_daily_totals(DB_PATH), indent=2))


if __name__ == '__main__':
    main()
