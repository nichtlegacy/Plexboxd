from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime

import pytest


ENV = {
    "DISCORD_TOKEN": "token",
    "PLEX_TOKEN": "plex-token",
    "PLEX_SERVER_URL": "http://plex:32400",
    "PLEX_USERNAME": "jan",
    "NOTIFY_CHANNEL_ID": "123",
    "GUILD_ID": "456",
}

WATCHED_AT = "2026-07-25T05:50:44"


@pytest.fixture
def utils_module(monkeypatch, tmp_path):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLEXBOXD_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    for name in ("plex_bot", "utils"):
        sys.modules.pop(name, None)
    return importlib.import_module("utils")


def _details(**overrides) -> dict:
    details = {
        "title": "Masters of the Universe",
        "year": 2026,
        "duration": "2h 5min",
        "genres": "Action, Fantasy",
        "directors": "Travis Knight",
        "rating": 7.4,
        "thumb": None,
        "ratingKey": "95551",
        "last_viewed_at": WATCHED_AT,
        "view_count": 1,
        "summary": "He-Man must save Eternia.",
        "tmdb_id": "454639",
        "library": "Filme",
    }
    details.update(overrides)
    return details


def _build(utils_module, **overrides):
    embed, _file = asyncio.run(utils_module.create_movie_embed(_details(**overrides)))
    return embed


def test_footer_clock_uses_the_watch_time(utils_module) -> None:
    """It used to show utcnow(), so "Watched" could be up to 15 minutes off."""
    embed = _build(utils_module)

    assert embed.timestamp is not None
    assert embed.timestamp.tzinfo is not None
    assert embed.timestamp.replace(tzinfo=None) == datetime.fromisoformat(WATCHED_AT)


def test_description_holds_only_the_summary(utils_module) -> None:
    """Discord renders the watch time itself, so the description must not repeat it."""
    embed = _build(utils_module)

    assert embed.description == "📜 **Description**: He-Man must save Eternia."


def test_footer_is_the_label_alone(utils_module) -> None:
    """Discord appends the embed timestamp after the footer text."""
    embed = _build(utils_module)

    assert embed.footer.text == "Watched"
    # No markup (it would render verbatim) and no hand-formatted date (it would
    # duplicate the timestamp Discord already shows).
    assert "<t:" not in embed.footer.text
    assert "2026" not in embed.footer.text


def test_last_viewed_uses_dynamic_timestamp_on_rewatch(utils_module) -> None:
    """Replaces a hardcoded d.m.Y string that ignored the viewer's timezone."""
    embed = _build(utils_module, view_count=2, previous_viewed_at="2026-03-01T21:15:00")

    field = next(f for f in embed.fields if "Last Viewed" in f.name)
    assert field.value == utils_module.discord_timestamp("2026-03-01T21:15:00", "R")


def test_embed_still_builds_without_a_watch_time(utils_module) -> None:
    """Falls back to now() rather than failing when Plex reports no watch date."""
    embed = _build(utils_module, last_viewed_at=None)

    assert embed.timestamp is not None
    assert "He-Man must save Eternia." in embed.description
