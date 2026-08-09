from __future__ import annotations

import importlib
import asyncio
import sys
from types import SimpleNamespace

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


def test_logs_are_anchored_to_project_root(monkeypatch, tmp_path) -> None:
    """A relative "logs" path wrote to /app/src/logs instead of the mounted volume."""
    from plexboxd.infrastructure.paths import resolve_data_path

    working_dir = tmp_path / "cwd"
    working_dir.mkdir()
    monkeypatch.setenv("PLEXBOXD_ROOT", str(tmp_path / "root"))
    # The default is relative, which is exactly the case that used to resolve against
    # the working directory. (The autouse fixture pins an absolute path; drop it here.)
    monkeypatch.delenv("PLEXBOXD_LOG_DIR", raising=False)
    monkeypatch.chdir(working_dir)
    module = _load_plex_bot(monkeypatch, dict(REQUIRED_ENV))

    module.setup_logging()

    assert resolve_data_path("logs") == tmp_path / "root" / "logs"
    assert (tmp_path / "root" / "logs").is_dir()
    # Nothing may be created relative to the working directory.
    assert not (working_dir / "logs").exists()


def test_letterboxd_logger_writes_to_its_own_file(monkeypatch, tmp_path) -> None:
    """The integration logger had no handlers, so diary failures reached no file."""
    import logging

    log_dir = tmp_path / "logs"
    monkeypatch.setenv("PLEXBOXD_LOG_DIR", str(log_dir))
    module = _load_plex_bot(monkeypatch, dict(REQUIRED_ENV))
    module.setup_logging()

    letterboxd_logger = logging.getLogger("LetterboxdIntegration")
    assert letterboxd_logger.handlers
    assert letterboxd_logger.propagate is False

    letterboxd_logger.error("diary write failed")
    for handler in letterboxd_logger.handlers:
        handler.flush()

    assert "diary write failed" in (log_dir / "letterboxd_integration.log").read_text(encoding="utf-8")


def test_file_handler_rotates_on_the_logged_clock(monkeypatch, tmp_path) -> None:
    """Timestamps are local, so rotation must not use UTC or it rolls mid-day."""
    import logging.handlers

    monkeypatch.setenv("PLEXBOXD_LOG_DIR", str(tmp_path / "logs"))
    module = _load_plex_bot(monkeypatch, dict(REQUIRED_ENV))
    module.setup_logging()

    handlers = [
        handler
        for handler in logging.getLogger("PlexBot").handlers
        if isinstance(handler, logging.handlers.TimedRotatingFileHandler)
    ]
    assert handlers
    assert all(handler.utc is False for handler in handlers)


def test_plex_outage_waits_for_next_loop_instead_of_restarting(monkeypatch, caplog) -> None:
    import requests

    module = _load_plex_bot(monkeypatch, dict(REQUIRED_ENV))

    class OfflinePlex:
        def history(self, **_kwargs):
            raise requests.ConnectionError("connection refused")

    monitor = SimpleNamespace(plex=OfflinePlex())
    bot = SimpleNamespace(notify_channel=object(), plex_monitor=monitor)

    asyncio.run(module.PlexDiscordBot.check_recently_watched.coro(bot))

    assert monitor.plex is None
    assert "retrying next interval" in caplog.text
