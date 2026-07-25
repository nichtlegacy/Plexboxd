from pathlib import Path

from plexboxd.domain.enums import RatingJobStatus
from plexboxd.domain.models import RatingJob, WatchEvent
from plexboxd.infrastructure.db.connection import Database
from plexboxd.infrastructure.db.migrations import apply_migrations
from plexboxd.infrastructure.db.repositories import RatingJobRepository, WatchEventRepository


def test_claim_next_locks_oldest_pending_job(tmp_path: Path, fixed_now) -> None:
    database = Database(tmp_path / "plexboxd.db")
    apply_migrations(database)
    watch_repository = WatchEventRepository(database)
    job_repository = RatingJobRepository(database)

    watch_repository.add(
        WatchEvent(
            id="watch-1",
            plex_rating_key="rk-1",
            title="Heat",
            watched_at=fixed_now,
            detected_at=fixed_now,
        )
    )
    watch_repository.add(
        WatchEvent(
            id="watch-2",
            plex_rating_key="rk-2",
            title="Drive",
            watched_at=fixed_now,
            detected_at=fixed_now,
        )
    )
    first_job = RatingJob(
        id="job-1",
        watch_event_id="watch-1",
        notification_id=None,
        requested_rating=4.0,
        requested_liked=True,
        requested_rewatch=False,
        requested_by_discord_user_id="123",
        status=RatingJobStatus.PENDING,
        created_at=fixed_now,
        updated_at=fixed_now,
    )
    second_job = RatingJob(
        id="job-2",
        watch_event_id="watch-2",
        notification_id=None,
        requested_rating=3.5,
        requested_liked=False,
        requested_rewatch=False,
        requested_by_discord_user_id="123",
        status=RatingJobStatus.PENDING,
        created_at=fixed_now.replace(minute=fixed_now.minute + 1),
        updated_at=fixed_now.replace(minute=fixed_now.minute + 1),
    )
    job_repository.add(first_job)
    job_repository.add(second_job)

    claimed = job_repository.claim_next(worker_id="worker-1", locked_at=fixed_now)

    assert claimed is not None
    assert claimed.id == "job-1"
    assert claimed.job_lock_owner == "worker-1"


def test_claim_next_returns_none_when_no_pending_job(tmp_path: Path, fixed_now) -> None:
    database = Database(tmp_path / "plexboxd.db")
    apply_migrations(database)
    job_repository = RatingJobRepository(database)

    claimed = job_repository.claim_next(worker_id="worker-1", locked_at=fixed_now)

    assert claimed is None
