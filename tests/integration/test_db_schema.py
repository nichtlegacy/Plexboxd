from pathlib import Path

from plexboxd.infrastructure.db.connection import Database
from plexboxd.infrastructure.db.migrations import apply_migrations


def test_apply_migrations_creates_expected_tables(tmp_path: Path) -> None:
    database = Database(tmp_path / "plexboxd.db")

    apply_migrations(database)

    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "watch_events" in tables
    assert "notifications" in tables
    assert "rating_jobs" in tables
    assert "rating_attempts" in tables
    assert "rating_results" in tables
    assert "film_match_cache" in tables


def test_apply_migrations_adds_log_entry_columns(tmp_path: Path) -> None:
    """The API migration needs the LID cache plus tag/review columns."""
    database = Database(tmp_path / "plexboxd.db")

    apply_migrations(database)

    with database.connect() as connection:
        job_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(rating_jobs)").fetchall()
        }
        cache_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(film_match_cache)").fetchall()
        }

    assert {"requested_tags", "requested_review"} <= job_columns
    assert "letterboxd_lid" in cache_columns


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "plexboxd.db")

    apply_migrations(database)
    first_pass = _applied_versions(database)

    apply_migrations(database)

    # Re-running must not duplicate rows; ALTER TABLE would otherwise fail outright.
    assert _applied_versions(database) == first_pass
    assert len(first_pass) == len(set(first_pass))


def _applied_versions(database: Database) -> list[str]:
    with database.connect() as connection:
        return [
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        ]
