from datetime import datetime, timezone

import pytest

from plexboxd.domain.enums import RatingJobStatus
from plexboxd.domain.models import RatingJob, RatingRequest


def test_rating_request_rejects_invalid_increment() -> None:
    with pytest.raises(ValueError):
        RatingRequest(rating=4.3, liked=False, rewatch=False)


def test_rating_request_rejects_out_of_range_rating() -> None:
    with pytest.raises(ValueError):
        RatingRequest(rating=5.5, liked=False, rewatch=False)


def test_rating_job_transition_rules() -> None:
    job = RatingJob(
        id="job-1",
        watch_event_id="watch-1",
        notification_id=None,
        requested_rating=4.5,
        requested_liked=True,
        requested_rewatch=False,
        requested_by_discord_user_id="123",
        status=RatingJobStatus.PENDING,
        created_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc),
    )

    assert job.can_transition_to(RatingJobStatus.RUNNING)
    assert not job.can_transition_to(RatingJobStatus.SUCCEEDED)
