from pathlib import Path

from plexboxd.application.rating_execution import RatingExecutionService
from plexboxd.application.rating_jobs import RatingJobService
from plexboxd.domain.enums import RatingAttemptStatus, RatingJobStatus
from plexboxd.domain.models import RatingRequest, WatchEvent
from plexboxd.infrastructure.db.connection import Database
from plexboxd.infrastructure.db.migrations import apply_migrations
from plexboxd.infrastructure.db.repositories import (
    RatingAttemptRepository,
    RatingJobRepository,
    RatingResultRepository,
    WatchEventRepository,
)
from plexboxd.infrastructure.queue.worker import RatingJobWorker


class StubMatcher:
    def resolve(self, _event):
        return type(
            "Match",
            (),
            {
                "letterboxd_film_id": "1234",
                "letterboxd_lid": "aBcD",
                "letterboxd_slug": "heat",
                "strategy": type("Strategy", (), {"value": "tmdb"})(),
            },
        )()


class StubWriter:
    def write(self, **kwargs):
        return {
            "write_strategy": "session",
            "letterboxd_entry_id": "entry-1",
            "watched_on": kwargs["watched_on"].isoformat(),
        }


class StubVerifier:
    def verify(self, **_kwargs):
        return None


def test_worker_run_once_processes_pending_job(tmp_path: Path, fixed_now, fake_clock, fake_id_factory) -> None:
    database = Database(tmp_path / "plexboxd.db")
    apply_migrations(database)
    watch_events = WatchEventRepository(database)
    rating_jobs = RatingJobRepository(database)
    rating_attempts = RatingAttemptRepository(database)
    rating_results = RatingResultRepository(database)

    watch_events.add(
        WatchEvent(
            id="watch-1",
            plex_rating_key="rk-1",
            title="Heat",
            watched_at=fixed_now,
            detected_at=fixed_now,
        )
    )
    rating_job_service = RatingJobService(
        repository=rating_jobs,
        result_repository=rating_results,
        id_factory=fake_id_factory,
        clock=fake_clock,
    )
    job = rating_job_service.enqueue(
        "watch-1",
        None,
        RatingRequest(rating=4.5, liked=True, rewatch=False),
    )
    execution_service = RatingExecutionService(
        attempt_repository=rating_attempts,
        result_repository=rating_results,
        matcher=StubMatcher(),
        writer=StubWriter(),
        verifier=StubVerifier(),
        id_factory=fake_id_factory,
        clock=fake_clock,
    )
    worker = RatingJobWorker(
        watch_event_repository=watch_events,
        rating_job_service=rating_job_service,
        rating_execution_service=execution_service,
    )

    processed = worker.run_once(worker_id="worker-1")

    assert processed is True
    updated_job = rating_jobs.get_by_id(job.id)
    assert updated_job is not None
    assert updated_job.status == RatingJobStatus.SUCCEEDED
    result = rating_results.get_by_watch_event("watch-1")
    assert result is not None


def test_worker_marks_job_failed_on_exception(tmp_path: Path, fixed_now, fake_clock, fake_id_factory) -> None:
    database = Database(tmp_path / "plexboxd.db")
    apply_migrations(database)
    watch_events = WatchEventRepository(database)
    rating_jobs = RatingJobRepository(database)
    rating_attempts = RatingAttemptRepository(database)
    rating_results = RatingResultRepository(database)

    watch_events.add(
        WatchEvent(
            id="watch-1",
            plex_rating_key="rk-1",
            title="Heat",
            watched_at=fixed_now,
            detected_at=fixed_now,
        )
    )
    rating_job_service = RatingJobService(
        repository=rating_jobs,
        result_repository=rating_results,
        id_factory=fake_id_factory,
        clock=fake_clock,
    )
    job = rating_job_service.enqueue(
        "watch-1",
        None,
        RatingRequest(rating=4.0, liked=False, rewatch=False),
    )

    class FailingMatcher:
        def resolve(self, _event):
            return None

    execution_service = RatingExecutionService(
        attempt_repository=rating_attempts,
        result_repository=rating_results,
        matcher=FailingMatcher(),
        writer=StubWriter(),
        verifier=StubVerifier(),
        id_factory=fake_id_factory,
        clock=fake_clock,
    )
    worker = RatingJobWorker(
        watch_event_repository=watch_events,
        rating_job_service=rating_job_service,
        rating_execution_service=execution_service,
    )

    processed = worker.run_once(worker_id="worker-1")

    assert processed is True
    updated_job = rating_jobs.get_by_id(job.id)
    assert updated_job is not None
    assert updated_job.status == RatingJobStatus.FAILED
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status FROM rating_attempts WHERE rating_job_id = ? ORDER BY rowid DESC LIMIT 1",
            (job.id,),
        ).fetchone()
    assert row is not None
    assert row["status"] == RatingAttemptStatus.FAILED
