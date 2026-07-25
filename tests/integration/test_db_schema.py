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


def test_relative_db_path_is_anchored_to_project_root(monkeypatch, tmp_path) -> None:
    """The CLI and worker default to a relative "data/plexboxd.db".

    Resolved against the working directory that pointed at a non-existent src/data/
    when run from src/ (which is the container's WORKDIR), so the CLI failed with
    "unable to open database file".
    """
    monkeypatch.setenv("PLEXBOXD_ROOT", str(tmp_path))
    working_dir = tmp_path / "elsewhere"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)

    database = Database("data/plexboxd.db")

    assert Path(database.path) == tmp_path / "data" / "plexboxd.db"
    assert not (working_dir / "data").exists()


def test_absolute_db_path_is_left_alone(tmp_path) -> None:
    explicit = tmp_path / "custom" / "plexboxd.db"

    assert Path(Database(explicit).path) == explicit
