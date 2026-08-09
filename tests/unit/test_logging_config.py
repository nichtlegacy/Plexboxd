from types import SimpleNamespace

from logging_config import _retry_after_seconds


def test_retry_after_prefers_discord_json_seconds() -> None:
    response = SimpleNamespace(
        json=lambda: {"retry_after": 1.68},
        headers={"Retry-After": "1680"},
    )

    assert _retry_after_seconds(response) == 1.68


def test_retry_after_converts_millisecond_header() -> None:
    response = SimpleNamespace(
        json=lambda: (_ for _ in ()).throw(ValueError()),
        headers={"Retry-After": "1680"},
    )

    assert _retry_after_seconds(response) == 1.68
