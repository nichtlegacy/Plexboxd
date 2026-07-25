from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from plexboxd.application.matching import MatchResolutionService
from plexboxd.application.rating_execution import RatingExecutionService
from plexboxd.application.rating_jobs import RatingJobService
from plexboxd.application.watch_ingest import WatchIngestService
from plexboxd.infrastructure.clock import SystemClock
from plexboxd.infrastructure.db.connection import Database
from plexboxd.infrastructure.db.migrations import apply_migrations
from plexboxd.infrastructure.db.repositories import (
    FilmMatchCacheRepository,
    NotificationRepository,
    RatingAttemptRepository,
    RatingJobRepository,
    RatingResultRepository,
    WatchEventRepository,
)
from plexboxd.infrastructure.ids import UuidIdFactory
from plexboxd.integrations.letterboxd.matcher import LetterboxdMatcher
from plexboxd.integrations.letterboxd.session import LetterboxdSessionProvider
from plexboxd.integrations.letterboxd.verifier import BasicLetterboxdVerifier
from plexboxd.integrations.letterboxd.writer import LetterboxdWriter


@dataclass(slots=True)
class ApplicationContainer:
    database: Database
    watch_events: WatchEventRepository
    notifications: NotificationRepository
    rating_jobs: RatingJobRepository
    rating_attempts: RatingAttemptRepository
    rating_results: RatingResultRepository
    film_match_cache: FilmMatchCacheRepository
    watch_ingest: WatchIngestService
    rating_job_service: RatingJobService
    matching_service: MatchResolutionService
    rating_execution_service: RatingExecutionService
    clock: SystemClock
    id_factory: UuidIdFactory


def build_application_container(db_path: str | Path = "data/plexboxd.db") -> ApplicationContainer:
    load_dotenv()
    database = Database(db_path)
    apply_migrations(database)

    clock = SystemClock()
    id_factory = UuidIdFactory()

    watch_events = WatchEventRepository(database)
    notifications = NotificationRepository(database)
    rating_jobs = RatingJobRepository(database)
    rating_attempts = RatingAttemptRepository(database)
    rating_results = RatingResultRepository(database)
    film_match_cache = FilmMatchCacheRepository(database)
    session_provider = LetterboxdSessionProvider()

    matching_service = MatchResolutionService(
        cache_repository=film_match_cache,
        provider=LetterboxdMatcher(session_provider=session_provider),
    )
    rating_execution_service = RatingExecutionService(
        attempt_repository=rating_attempts,
        result_repository=rating_results,
        matcher=matching_service,
        writer=LetterboxdWriter(session_provider=session_provider),
        verifier=BasicLetterboxdVerifier(),
        id_factory=id_factory,
        clock=clock,
    )

    return ApplicationContainer(
        database=database,
        watch_events=watch_events,
        notifications=notifications,
        rating_jobs=rating_jobs,
        rating_attempts=rating_attempts,
        rating_results=rating_results,
        film_match_cache=film_match_cache,
        watch_ingest=WatchIngestService(repository=watch_events),
        rating_job_service=RatingJobService(
            repository=rating_jobs,
            result_repository=rating_results,
            id_factory=id_factory,
            clock=clock,
        ),
        matching_service=matching_service,
        rating_execution_service=rating_execution_service,
        clock=clock,
        id_factory=id_factory,
    )
