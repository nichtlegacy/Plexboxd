from __future__ import annotations

import importlib
import sqlite3
import sys

import pytest


ENV = {
    "DISCORD_TOKEN": "token",
    "PLEX_TOKEN": "plex-token",
    "PLEX_SERVER_URL": "http://plex:32400",
    "PLEX_USERNAME": "jan",
    "NOTIFY_CHANNEL_ID": "123",
    "GUILD_ID": "456",
}


@pytest.fixture
def movie_db(monkeypatch, tmp_path):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    sys.modules.pop("plex_bot", None)
    plex_bot = importlib.import_module("plex_bot")
    return plex_bot.MovieDatabase(tmp_path / "movies.db"), tmp_path / "movies.db"


def _rows(path, rating_key):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(
            "SELECT is_rated FROM movies WHERE rating_key = ?", (rating_key,)
        ).fetchall()
    finally:
        connection.close()


def test_mark_as_rated_persists(movie_db) -> None:
    """The connection helper never committed, so this update used to be discarded."""
    db, path = movie_db
    with db._get_connection() as connection:
        connection.execute(
            "INSERT INTO movies (rating_key, title, is_rated) VALUES ('rk1', 'X', 0)"
        )

    db.mark_as_rated("rk1")

    assert _rows(path, "rk1") == [(1,)]


def test_writes_are_rolled_back_on_error(movie_db) -> None:
    db, path = movie_db

    with pytest.raises(RuntimeError):
        with db._get_connection() as connection:
            connection.execute("INSERT INTO movies (rating_key, title) VALUES ('rk2', 'Y')")
            raise RuntimeError("boom")

    assert _rows(path, "rk2") == []


def test_schema_is_created_on_init(movie_db) -> None:
    """Table creation ran inside the same uncommitted block."""
    _db, path = movie_db
    connection = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        connection.close()

    assert "movies" in tables
