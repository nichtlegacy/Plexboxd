from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .connection import Database


SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


def apply_migrations(database: Database) -> None:
    schema_files = sorted(SCHEMA_DIR.glob("*.sql"))
    with database.connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        for schema_file in schema_files:
            version = schema_file.stem
            applied = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?",
                (version,),
            ).fetchone()
            if applied:
                continue

            connection.executescript(schema_file.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
