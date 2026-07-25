from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from plexboxd.infrastructure.paths import resolve_data_path


class Database:
    def __init__(self, path: str | Path):
        # Anchor relative paths to the project root. The CLI and worker default to
        # "data/plexboxd.db", and the bot runs from src/ (WORKDIR /app/src in Docker), so
        # resolving against the working directory pointed them at a non-existent
        # src/data/ instead of the real database.
        self.path = str(resolve_data_path(path))

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
