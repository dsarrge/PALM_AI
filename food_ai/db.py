from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path

from food_ai.config import DB_PATH, SCHEMA_PATH


def connect_db(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def initialize_db(db_path: str | Path = DB_PATH, schema_path: str | Path = SCHEMA_PATH) -> Path:
    destination = Path(db_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    schema = Path(schema_path)
    if schema.exists():
        with closing(sqlite3.connect(destination)) as connection:
            connection.executescript(schema.read_text(encoding='utf-8'))
            connection.commit()
    return destination
