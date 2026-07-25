from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any

from .enums import ErrorType, MatchStrategy, RatingAttemptStatus, RatingJobStatus, WriteStrategy


@dataclass(slots=True, frozen=True)
class WatchEvent:
    id: str
    plex_rating_key: str
    title: str
    watched_at: datetime
    detected_at: datetime
    original_title: str | None = None
    tmdb_id: str | None = None
    year: int | None = None
    view_count_at_watch: int | None = None
    library_name: str | None = None
    plex_guid_hash: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class NotificationRecord:
    id: str
    watch_event_id: str
    discord_channel_id: str
    discord_message_id: str
    discord_view_state: str
    sent_at: datetime
    updated_at: datetime


@dataclass(slots=True, frozen=True)
class RatingRequest:
    rating: float
    liked: bool
    rewatch: bool
    requested_by_discord_user_id: str | None = None
    tags: tuple[str, ...] = ()
    review: str = ""

    def __post_init__(self) -> None:
        if self.rating < 0.5 or self.rating > 5.0:
            raise ValueError("rating must be between 0.5 and 5.0")
        if (self.rating * 2) % 1 != 0:
            raise ValueError("rating must be in 0.5 increments")


@dataclass(slots=True, frozen=True)
class RatingJob:
    id: str
    watch_event_id: str
    notification_id: str | None
    requested_rating: float
    requested_liked: bool
    requested_rewatch: bool
    requested_by_discord_user_id: str | None
    status: RatingJobStatus
    created_at: datetime
    updated_at: datetime
    job_locked_at: datetime | None = None
    job_lock_owner: str | None = None
    requested_tags: tuple[str, ...] = ()
    requested_review: str = ""

    def can_transition_to(self, new_status: RatingJobStatus) -> bool:
        transitions = {
            RatingJobStatus.PENDING: {
                RatingJobStatus.MATCHED,
                RatingJobStatus.RUNNING,
                RatingJobStatus.FAILED,
                RatingJobStatus.CANCELLED,
                RatingJobStatus.MANUAL_ACTION,
            },
            RatingJobStatus.MATCHED: {
                RatingJobStatus.RUNNING,
                RatingJobStatus.FAILED,
                RatingJobStatus.MANUAL_ACTION,
                RatingJobStatus.CANCELLED,
            },
            RatingJobStatus.RUNNING: {
                RatingJobStatus.SUCCEEDED,
                RatingJobStatus.FAILED,
                RatingJobStatus.MANUAL_ACTION,
            },
            RatingJobStatus.FAILED: {
                RatingJobStatus.PENDING,
                RatingJobStatus.CANCELLED,
            },
            RatingJobStatus.MANUAL_ACTION: {
                RatingJobStatus.PENDING,
                RatingJobStatus.CANCELLED,
            },
            RatingJobStatus.SUCCEEDED: set(),
            RatingJobStatus.CANCELLED: set(),
        }
        return new_status in transitions[self.status]


@dataclass(slots=True, frozen=True)
class MatchCandidate:
    letterboxd_film_id: str
    letterboxd_slug: str
    candidate_title: str
    candidate_year: int | None
    score: float
    decision_reason: str
    letterboxd_lid: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class FilmMatch:
    letterboxd_film_id: str
    letterboxd_slug: str
    strategy: MatchStrategy
    confidence: float
    # Base-62 identifier (e.g. "gdKW") required as productionId by the /api/v0 write
    # endpoint. The numeric letterboxd_film_id is rejected there but stays the cache key.
    letterboxd_lid: str | None = None
    candidates: tuple[MatchCandidate, ...] = ()


@dataclass(slots=True, frozen=True)
class RatingAttempt:
    id: str
    rating_job_id: str
    attempt_no: int
    status: RatingAttemptStatus
    started_at: datetime
    match_strategy: MatchStrategy | None = None
    write_strategy: WriteStrategy | None = None
    finished_at: datetime | None = None
    error_type: ErrorType | None = None
    error_message: str | None = None
    debug_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RatingResult:
    id: str
    watch_event_id: str
    rating_job_id: str
    rating_attempt_id: str
    letterboxd_film_id: str
    rating_value: float
    liked: bool
    rewatch: bool
    watched_on: date
    succeeded_at: datetime
    letterboxd_entry_id: str | None = None
