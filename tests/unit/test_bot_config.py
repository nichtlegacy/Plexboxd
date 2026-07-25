from __future__ import annotations

import importlib
import sys

import pytest


REQUIRED_ENV = {
    "DISCORD_TOKEN": "token",
    "PLEX_TOKEN": "plex-token",
    "PLEX_SERVER_URL": "http://plex:32400",
    "PLEX_USERNAME": "jan",
    "NOTIFY_CHANNEL_ID": "123",
    "GUILD_ID": "456",
}


def _load_plex_bot(monkeypatch, env: dict[str, str]):
    for key in list(REQUIRED_ENV) + ["EXCLUDED_LIBRARIES", "DISCORD_USER_ID"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # The module reads its settings at import time, so it has to be re-imported per case.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    sys.modules.pop("plex_bot", None)
    return importlib.import_module("plex_bot")


def test_missing_required_setting_names_the_variable(monkeypatch) -> None:
    """A bare int(os.getenv(...)) used to fail with an opaque TypeError."""
    env = dict(REQUIRED_ENV)
    del env["NOTIFY_CHANNEL_ID"]

    with pytest.raises(SystemExit) as excinfo:
        _load_plex_bot(monkeypatch, env)

    assert "NOTIFY_CHANNEL_ID" in str(excinfo.value)


def test_non_numeric_channel_id_is_reported(monkeypatch) -> None:
    env = dict(REQUIRED_ENV, NOTIFY_CHANNEL_ID="not-a-number")

    with pytest.raises(SystemExit) as excinfo:
        _load_plex_bot(monkeypatch, env)

    assert "NOTIFY_CHANNEL_ID" in str(excinfo.value)
    assert "must be a number" in str(excinfo.value)


def test_version_comparison_is_numeric(monkeypatch) -> None:
    """String comparison breaks once a component reaches two digits."""
    module = _load_plex_bot(monkeypatch, dict(REQUIRED_ENV))

    assert module._version_tuple("1.10.0") > module._version_tuple("1.3.0")
    assert module._version_tuple("1.3.0") > module._version_tuple("1.2.8")
    assert module._version_tuple("1.3.0") == module._version_tuple("1.3.0")
    # Tags may carry suffixes; they must not raise.
    assert module._version_tuple("1.3.0-rc1") >= module._version_tuple("1.3.0")
    assert module._version_tuple("") == (0,)
