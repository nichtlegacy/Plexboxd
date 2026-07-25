from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import count

import pytest


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
