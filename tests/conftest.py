from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import count

import pytest


@pytest.fixture(autouse=True)
def isolate_runtime_side_effects(monkeypatch, tmp_path_factory):
    """Keep tests out of the real logs/ directory and away from the live webhook.

    Importing plex_bot configures logging, so a test that logs an expected error wrote
    into the operator's actual plex_bot.log — and, whenever the webhook variable was
    present in the environment, forwarded that noise to Discord.
    """
    monkeypatch.setenv("PLEXBOXD_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    monkeypatch.delenv("DISCORD_LOGGING_WEBHOOK_URL", raising=False)
    # .env must never bleed into a test run.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)


@dataclass
class FakeClock:
    current: datetime

    def now(self) -> datetime:
        return self.current


class FakeIdFactory:
    def __init__(self) -> None:
        self._counter = count(1)

    def new(self, prefix: str) -> str:
        return f"{prefix}-{next(self._counter)}"


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake_clock(fixed_now: datetime) -> FakeClock:
    return FakeClock(current=fixed_now)


@pytest.fixture
def fake_id_factory() -> FakeIdFactory:
    return FakeIdFactory()
