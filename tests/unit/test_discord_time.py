from __future__ import annotations

from datetime import datetime, timedelta, timezone

from discord_time import discord_timestamp, to_aware_datetime


def test_naive_value_is_treated_as_local_time() -> None:
    """plexapi returns naive datetimes in the host's local time, not UTC."""
    naive = datetime(2026, 7, 25, 5, 50, 44)

    aware = to_aware_datetime(naive)

    assert aware is not None
    assert aware.tzinfo is not None
    # Same wall clock, now carrying the local offset, so the instant is preserved.
    assert aware.replace(tzinfo=None) == naive
    assert aware.timestamp() == naive.astimezone().timestamp()


def test_iso_strings_are_accepted() -> None:
    aware = to_aware_datetime("2026-07-25T05:50:44")

    assert aware is not None
    assert aware.replace(tzinfo=None) == datetime(2026, 7, 25, 5, 50, 44)


def test_existing_timezone_is_preserved() -> None:
    original = datetime(2026, 7, 25, 3, 50, 44, tzinfo=timezone.utc)

    assert to_aware_datetime(original) == original


def test_unparseable_values_return_none() -> None:
    assert to_aware_datetime(None) is None
    assert to_aware_datetime("not a date") is None
    assert to_aware_datetime(12345) is None


def test_timestamp_markup_uses_unix_seconds() -> None:
    """Discord renders <t:...> in each viewer's own timezone."""
    moment = datetime(2026, 7, 25, 3, 50, 44, tzinfo=timezone.utc)

    assert discord_timestamp(moment, "R") == "<t:1784951444:R>"
    assert discord_timestamp(moment, "f") == "<t:1784951444:f>"


def test_timestamp_markup_defaults_to_relative() -> None:
    moment = datetime(2026, 7, 25, 3, 50, 44, tzinfo=timezone.utc)

    assert discord_timestamp(moment).endswith(":R>")


def test_timestamp_markup_survives_the_naive_roundtrip() -> None:
    """A naive Plex value must map to the instant it actually represents."""
    naive = datetime.now().replace(microsecond=0) - timedelta(hours=3)

    rendered = discord_timestamp(naive, "R")

    assert rendered is not None
    epoch = int(rendered.removeprefix("<t:").split(":", 1)[0])
    assert datetime.fromtimestamp(epoch) == naive


def test_timestamp_markup_returns_none_for_missing_values() -> None:
    """Callers fall back to plain text, so None has to be distinguishable."""
    assert discord_timestamp(None) is None
    assert discord_timestamp("") is None
