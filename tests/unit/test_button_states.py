from __future__ import annotations

import asyncio
import importlib
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

# Discord truncates button labels past roughly this width, well below the 80-char cap.
LABEL_BUDGET = 34


@pytest.fixture
def views(monkeypatch, tmp_path):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLEXBOXD_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    for name in ("plex_bot", "views"):
        sys.modules.pop(name, None)
    return importlib.import_module("views")


def _button(views_module):
    """Build the view inside a loop: discord.ui.View needs a running event loop."""

    async def build():
        view = views_module.MovieButtons(movie_title="Heat", movie_year=1995)
        return view, view.diary_button

    return asyncio.run(build())


@pytest.mark.parametrize(
    "rating, expected",
    [
        (1.0, "★"),
        (2.0, "★★"),
        (3.0, "★★★"),
        (5.0, "★★★★★"),
        (1.5, "★½"),
        (2.5, "★★½"),
        (4.5, "★★★★½"),
    ],
)
def test_stars_render_like_letterboxd(views, rating, expected) -> None:
    assert views.format_stars(rating) == expected


def test_half_star_alone_stays_readable(views) -> None:
    """A lone "½" reads as nothing, so the lowest rating spells itself out."""
    assert views.format_stars(0.5) == "½ star"


def test_stars_tolerate_bad_input(views) -> None:
    assert views.format_stars(None) == "None"
    assert views.format_stars("abc") == "abc"


def test_succeeded_label_has_no_date(views) -> None:
    """Labels are plain text, so a date could not be localised and showed server time.

    The embed timestamp already reports when the film was watched.
    """
    view, button = _button(views)

    view.mark_succeeded(rating=3.5)

    assert button.label == "Logged ★★★½"
    assert button.disabled is True
    for marker in ("2026", ":", "/", "."):
        assert marker not in button.label


def test_state_labels_stay_within_the_display_budget(views) -> None:
    view, button = _button(views)

    view.mark_queued()
    assert len(button.label) <= LABEL_BUDGET
    view.mark_succeeded(rating=5.0)
    assert len(button.label) <= LABEL_BUDGET
    view.mark_failed()
    assert len(button.label) <= LABEL_BUDGET


def test_states_are_visually_distinct(views) -> None:
    import discord

    view, button = _button(views)

    view.mark_queued()
    queued = (button.label, button.style, button.disabled)
    view.mark_succeeded(rating=4.0)
    succeeded = (button.label, button.style, button.disabled)
    view.mark_failed()
    failed = (button.label, button.style, button.disabled)

    assert len({queued, succeeded, failed}) == 3
    assert succeeded[1] is discord.ButtonStyle.success
    assert failed[1] is discord.ButtonStyle.danger
    # Only the failed state invites another click.
    assert failed[2] is False
    assert queued[2] is True and succeeded[2] is True
