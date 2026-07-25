from pathlib import Path

from plexboxd.domain.enums import RatingJobStatus
from plexboxd.domain.models import RatingJob, WatchEvent
from plexboxd.infrastructure.db.connection import Database
from plexboxd.infrastructure.db.migrations import apply_migrations
from plexboxd.infrastructure.db.repositories import RatingJobRepository, WatchEventRepository
from plexboxd.interfaces.cli import main


def test_inspect_job_command_returns_success(tmp_path: Path, fixed_now, capsys) -> None:
    db_path = tmp_path / "plexboxd.db"
    database = Database(db_path)
    apply_migrations(database)
    watch_events = WatchEventRepository(database)
    rating_jobs = RatingJobRepository(database)

    watch_events.add(
        WatchEvent(
            id="watch-1",
            plex_rating_key="rk-1",
            title="Heat",
            watched_at=fixed_now,
            detected_at=fixed_now,
        )
    )
    rating_jobs.add(
        RatingJob(
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
    )

    exit_code = main(["--db-path", str(db_path), "inspect-job", "job-1"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"id": "job-1"' in captured.out


def test_list_failed_jobs_command_returns_jobs(tmp_path: Path, fixed_now, capsys) -> None:
    db_path = tmp_path / "plexboxd.db"
    database = Database(db_path)
    apply_migrations(database)
    watch_events = WatchEventRepository(database)
    rating_jobs = RatingJobRepository(database)

    watch_events.add(
        WatchEvent(
            id="watch-1",
            plex_rating_key="rk-1",
            title="Heat",
            watched_at=fixed_now,
            detected_at=fixed_now,
        )
    )
    rating_jobs.add(
        RatingJob(
            id="job-1",
            watch_event_id="watch-1",
            notification_id=None,
            requested_rating=4.0,
            requested_liked=True,
            requested_rewatch=False,
            requested_by_discord_user_id="123",
            status=RatingJobStatus.FAILED,
            created_at=fixed_now,
            updated_at=fixed_now,
        )
    )

    exit_code = main(["--db-path", str(db_path), "list-failed-jobs"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "failed"' in captured.out


def test_retry_job_command_requeues_job(tmp_path: Path, fixed_now, capsys) -> None:
    db_path = tmp_path / "plexboxd.db"
    database = Database(db_path)
    apply_migrations(database)
    watch_events = WatchEventRepository(database)
    rating_jobs = RatingJobRepository(database)

    watch_events.add(
        WatchEvent(
            id="watch-1",
            plex_rating_key="rk-1",
            title="Heat",
            watched_at=fixed_now,
            detected_at=fixed_now,
        )
    )
    rating_jobs.add(
        RatingJob(
            id="job-1",
            watch_event_id="watch-1",
            notification_id=None,
            requested_rating=4.0,
            requested_liked=True,
            requested_rewatch=False,
            requested_by_discord_user_id="123",
            status=RatingJobStatus.FAILED,
            created_at=fixed_now,
            updated_at=fixed_now,
        )
    )

    exit_code = main(["--db-path", str(db_path), "retry-job", "job-1"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "pending"' in captured.out
