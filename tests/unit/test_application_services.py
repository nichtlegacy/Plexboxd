from __future__ import annotations

from dataclasses import replace

import pytest

from plexboxd.application.rating_jobs import RatingJobAlreadyCompletedError, RatingJobService
from plexboxd.application.watch_ingest import WatchIngestService
from plexboxd.domain.enums import RatingJobStatus
from plexboxd.domain.models import RatingJob, RatingRequest, WatchEvent


class InMemoryWatchRepository:
    def __init__(self) -> None:
        self.events: list[WatchEvent] = []

    def get_by_event_identity(self, *, plex_rating_key: str, watched_at):
        for event in self.events:
            if event.plex_rating_key == plex_rating_key and event.watched_at == watched_at:
                return event
        return None

    def add(self, event: WatchEvent) -> WatchEvent:
        self.events.append(event)
        return event


class InMemoryResultRepository:
    def __init__(self, exists: bool = False) -> None:
        self.exists = exists

    def exists_for_watch_event(self, watch_event_id: str) -> bool:
        return self.exists


class InMemoryJobRepository:
    def __init__(self) -> None:
        self.jobs: list[RatingJob] = []

    def get_active_for_watch_event(self, watch_event_id: str):
        for job in self.jobs:
            if job.watch_event_id == watch_event_id and job.status in {
                RatingJobStatus.PENDING,
                RatingJobStatus.MATCHED,
                RatingJobStatus.RUNNING,
                RatingJobStatus.MANUAL_ACTION,
            }:
                return job
        return None

    def add(self, job: RatingJob) -> RatingJob:
        self.jobs.append(job)
        return job

    def update_status(self, job_id: str, new_status: RatingJobStatus, updated_at):
        for index, job in enumerate(self.jobs):
            if job.id == job_id:
                updated_job = replace(job, status=new_status, updated_at=updated_at)
                self.jobs[index] = updated_job
                return updated_job
        raise KeyError(job_id)

    def claim_next(self, worker_id: str, locked_at):
        for index, job in enumerate(self.jobs):
            if job.status == RatingJobStatus.PENDING:
                claimed_job = replace(job, job_lock_owner=worker_id, job_locked_at=locked_at)
                self.jobs[index] = claimed_job
                return claimed_job
        return None


def test_watch_ingest_is_idempotent(fixed_now) -> None:
    repository = InMemoryWatchRepository()
    service = WatchIngestService(repository=repository)
    event = WatchEvent(
        id="watch-1",
        plex_rating_key="rk-1",
        title="Heat",
        watched_at=fixed_now,
        detected_at=fixed_now,
    )

    first = service.ingest(event)
    second = service.ingest(event)

    assert first is second
    assert len(repository.events) == 1


def test_enqueue_returns_existing_active_job(fake_clock, fake_id_factory) -> None:
    repository = InMemoryJobRepository()
    result_repository = InMemoryResultRepository()
    service = RatingJobService(
        repository=repository,
        result_repository=result_repository,
        id_factory=fake_id_factory,
        clock=fake_clock,
    )
    request = RatingRequest(rating=4.0, liked=True, rewatch=False)

    first = service.enqueue("watch-1", "notification-1", request)
    second = service.enqueue("watch-1", "notification-1", request)

    assert first is second
    assert len(repository.jobs) == 1


def test_enqueue_rejects_already_completed_watch_event(fake_clock, fake_id_factory) -> None:
    service = RatingJobService(
        repository=InMemoryJobRepository(),
        result_repository=InMemoryResultRepository(exists=True),
        id_factory=fake_id_factory,
        clock=fake_clock,
    )

    with pytest.raises(RatingJobAlreadyCompletedError):
        service.enqueue(
            "watch-1",
            "notification-1",
            RatingRequest(rating=3.5, liked=False, rewatch=True),
        )


def test_enqueue_carries_tags_and_review(fake_clock, fake_id_factory) -> None:
    """The Discord modal collects both; they must survive onto the job."""
    repository = InMemoryJobRepository()
    service = RatingJobService(
        repository=repository,
        result_repository=InMemoryResultRepository(),
        id_factory=fake_id_factory,
        clock=fake_clock,
    )

    job = service.enqueue(
        "watch-1",
        None,
        RatingRequest(
            rating=4.0,
            liked=True,
            rewatch=False,
            tags=("plex", "imported"),
            review="Loved it.",
        ),
    )

    assert job.requested_tags == ("plex", "imported")
    assert job.requested_review == "Loved it."


def test_match_resolution_skips_cache_without_lid() -> None:
    """A pre-migration cache row has no LID, so it must not be used as-is."""
    from plexboxd.application.matching import MatchResolutionService

    class CacheRepository:
        def get_by_tmdb_id(self, _tmdb_id):
            return {
                "id": "cache-1",
                "letterboxd_film_id": "27470",
                "letterboxd_slug": "human-traffic",
                "confidence": 1.0,
            }

    class Provider:
        def __init__(self) -> None:
            self.tmdb_calls = 0

        def match_by_tmdb(self, _event):
            self.tmdb_calls += 1
            return None

        def search_candidates(self, _event):
            return []

    provider = Provider()
    service = MatchResolutionService(cache_repository=CacheRepository(), provider=provider)
    event = WatchEvent(
        id="watch-1",
        plex_rating_key="rk-1",
        title="Human Traffic",
        tmdb_id="11129",
        watched_at=None,
        detected_at=None,
    )

    assert service.resolve(event) is None
    # Falling through to live resolution rather than writing with a null productionId.
    assert provider.tmdb_calls == 1


def test_match_resolution_backfills_lid_from_slug() -> None:
    from plexboxd.application.matching import MatchResolutionService

    persisted: dict[str, str] = {}

    class CacheRepository:
        def get_by_tmdb_id(self, _tmdb_id):
            return {
                "id": "cache-1",
                "letterboxd_film_id": "27470",
                "letterboxd_slug": "human-traffic",
                "confidence": 1.0,
            }

        def set_lid(self, cache_id, lid):
            persisted[cache_id] = lid

    class Provider:
        def resolve_lid_for_slug(self, _slug):
            return "gdKW"

        def match_by_tmdb(self, _event):  # pragma: no cover - cache hit wins
            raise AssertionError("should not re-resolve when the LID can be backfilled")

    service = MatchResolutionService(cache_repository=CacheRepository(), provider=Provider())
    event = WatchEvent(
        id="watch-1",
        plex_rating_key="rk-1",
        title="Human Traffic",
        tmdb_id="11129",
        watched_at=None,
        detected_at=None,
    )

    match = service.resolve(event)

    assert match is not None
    assert match.letterboxd_lid == "gdKW"
    assert persisted == {"cache-1": "gdKW"}
