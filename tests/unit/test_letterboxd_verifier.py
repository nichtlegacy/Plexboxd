from __future__ import annotations

from types import SimpleNamespace

import pytest

from plexboxd.integrations.letterboxd.verifier import BasicLetterboxdVerifier


def _job(rating: float = 4.5, liked: bool = True, rewatch: bool = False):
    return SimpleNamespace(
        requested_rating=rating,
        requested_liked=liked,
        requested_rewatch=rewatch,
    )


def _write_result(**overrides):
    result = {
        "letterboxd_entry_id": "fqHMA7",
        "watched_on": "2026-05-07",
        "diary_date": "2026-05-07",
        "rating": 4.5,
        "liked": True,
        "rewatch": False,
    }
    result.update(overrides)
    return result


def test_verify_accepts_matching_write() -> None:
    BasicLetterboxdVerifier().verify(write_result=_write_result(), event=object(), job=_job())


def test_verify_accepts_shifted_date_from_threshold_hour() -> None:
    """The writer shifts early-morning watches back a day; that is not a mismatch."""
    result = _write_result(watched_on="2026-05-06", diary_date="2026-05-06")

    BasicLetterboxdVerifier().verify(write_result=result, event=object(), job=_job())


def test_verify_rejects_missing_entry_id() -> None:
    with pytest.raises(RuntimeError, match="no entry id"):
        BasicLetterboxdVerifier().verify(
            write_result=_write_result(letterboxd_entry_id=None), event=object(), job=_job()
        )


def test_verify_rejects_rating_mismatch() -> None:
    """This is the case that previously passed silently and recorded a wrong rating."""
    with pytest.raises(RuntimeError, match="rating mismatch"):
        BasicLetterboxdVerifier().verify(
            write_result=_write_result(rating=3.0), event=object(), job=_job(rating=4.5)
        )


def test_verify_rejects_like_mismatch() -> None:
    with pytest.raises(RuntimeError, match="like mismatch"):
        BasicLetterboxdVerifier().verify(
            write_result=_write_result(liked=False), event=object(), job=_job(liked=True)
        )


def test_verify_rejects_rewatch_mismatch() -> None:
    with pytest.raises(RuntimeError, match="rewatch mismatch"):
        BasicLetterboxdVerifier().verify(
            write_result=_write_result(rewatch=False), event=object(), job=_job(rewatch=True)
        )


def test_verify_rejects_diary_date_mismatch() -> None:
    with pytest.raises(RuntimeError, match="diary date mismatch"):
        BasicLetterboxdVerifier().verify(
            write_result=_write_result(diary_date="2026-05-01"), event=object(), job=_job()
        )


def test_verify_tolerates_float_rating_representation() -> None:
    """The API echoes 3 as 3.0; that must not read as a mismatch."""
    BasicLetterboxdVerifier().verify(
        write_result=_write_result(rating=3.0), event=object(), job=_job(rating=3)
    )
