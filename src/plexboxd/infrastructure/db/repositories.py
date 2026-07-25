from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from plexboxd.domain.enums import RatingAttemptStatus, RatingJobStatus
from plexboxd.domain.models import NotificationRecord, RatingAttempt, RatingJob, RatingResult, WatchEvent

from .connection import Database


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _date(value: str) -> date:
    return date.fromisoformat(value)


class WatchEventRepository:
    def __init__(self, database: Database):
        self.database = database

    def get_by_event_identity(self, *, plex_rating_key: str, watched_at: datetime) -> WatchEvent | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM watch_events WHERE plex_rating_key = ? AND watched_at = ?",
                (plex_rating_key, watched_at.isoformat()),
            ).fetchone()
        return _watch_event_from_row(row) if row else None

    def get_by_id(self, watch_event_id: str) -> WatchEvent | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM watch_events WHERE id = ?",
                (watch_event_id,),
            ).fetchone()
        return _watch_event_from_row(row) if row else None

    def add(self, event: WatchEvent) -> WatchEvent:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO watch_events (
                    id, plex_rating_key, plex_guid_hash, tmdb_id, title, original_title, year,
                    watched_at, view_count_at_watch, library_name, raw_payload_json, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.plex_rating_key,
                    event.plex_guid_hash,
                    event.tmdb_id,
                    event.title,
                    event.original_title,
                    event.year,
                    event.watched_at.isoformat(),
                    event.view_count_at_watch,
                    event.library_name,
                    json.dumps(event.raw_payload),
                    event.detected_at.isoformat(),
                ),
            )
        return event


class RatingResultRepository:
    def __init__(self, database: Database):
        self.database = database

    def exists_for_watch_event(self, watch_event_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM rating_results WHERE watch_event_id = ?",
                (watch_event_id,),
            ).fetchone()
        return row is not None

    def get_by_watch_event(self, watch_event_id: str) -> RatingResult | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM rating_results WHERE watch_event_id = ?",
                (watch_event_id,),
            ).fetchone()
        return _rating_result_from_row(row) if row else None

    def add(self, result: RatingResult) -> RatingResult:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO rating_results (
                    id, watch_event_id, rating_job_id, rating_attempt_id, letterboxd_film_id,
                    letterboxd_entry_id, rating_value, liked, rewatch, watched_on, succeeded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.id,
                    result.watch_event_id,
                    result.rating_job_id,
                    result.rating_attempt_id,
                    result.letterboxd_film_id,
                    result.letterboxd_entry_id,
                    result.rating_value,
                    int(result.liked),
                    int(result.rewatch),
                    result.watched_on.isoformat(),
                    result.succeeded_at.isoformat(),
                ),
            )
        return result


class RatingJobRepository:
    ACTIVE_STATUSES = (
        RatingJobStatus.PENDING.value,
        RatingJobStatus.MATCHED.value,
        RatingJobStatus.RUNNING.value,
        RatingJobStatus.MANUAL_ACTION.value,
    )

    def __init__(self, database: Database):
        self.database = database

    def get_active_for_watch_event(self, watch_event_id: str) -> RatingJob | None:
        with self.database.connect() as connection:
            placeholders = ",".join("?" * len(self.ACTIVE_STATUSES))
            row = connection.execute(
                f"""
                SELECT * FROM rating_jobs
                WHERE watch_event_id = ? AND status IN ({placeholders})
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (watch_event_id, *self.ACTIVE_STATUSES),
            ).fetchone()
        return _rating_job_from_row(row) if row else None

    def get_by_id(self, job_id: str) -> RatingJob | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM rating_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return _rating_job_from_row(row) if row else None

    def list_by_status(self, status: RatingJobStatus) -> list[RatingJob]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM rating_jobs WHERE status = ? ORDER BY created_at ASC",
                (status.value,),
            ).fetchall()
        return [_rating_job_from_row(row) for row in rows]

    def add(self, job: RatingJob) -> RatingJob:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO rating_jobs (
                    id, watch_event_id, notification_id, status, requested_rating, requested_liked,
                    requested_rewatch, requested_by_discord_user_id, job_locked_at, job_lock_owner,
                    created_at, updated_at, requested_tags, requested_review
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.watch_event_id,
                    job.notification_id,
                    job.status.value,
                    job.requested_rating,
                    int(job.requested_liked),
                    int(job.requested_rewatch),
                    job.requested_by_discord_user_id,
                    job.job_locked_at.isoformat() if job.job_locked_at else None,
                    job.job_lock_owner,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                    json.dumps(list(job.requested_tags)),
                    job.requested_review,
                ),
            )
        return job

    def update_status(self, job_id: str, new_status: RatingJobStatus, updated_at: datetime) -> RatingJob:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE rating_jobs
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_status.value, updated_at.isoformat(), job_id),
            )
        job = self.get_by_id(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def claim_next(self, worker_id: str, locked_at: datetime) -> RatingJob | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM rating_jobs
                WHERE status = ?
                  AND job_lock_owner IS NULL
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (RatingJobStatus.PENDING.value,),
            ).fetchone()
            if row is None:
                return None

            updated = connection.execute(
                """
                UPDATE rating_jobs
                SET job_lock_owner = ?, job_locked_at = ?, updated_at = ?
                WHERE id = ? AND job_lock_owner IS NULL
                """,
                (worker_id, locked_at.isoformat(), locked_at.isoformat(), row["id"]),
            )
            if updated.rowcount != 1:
                return None

        return self.get_by_id(row["id"])

    def requeue(self, job_id: str, updated_at: datetime) -> RatingJob:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE rating_jobs
                SET status = ?, updated_at = ?, job_lock_owner = NULL, job_locked_at = NULL
                WHERE id = ?
                """,
                (RatingJobStatus.PENDING.value, updated_at.isoformat(), job_id),
            )
        job = self.get_by_id(job_id)
        if job is None:
            raise KeyError(job_id)
        return job


class RatingAttemptRepository:
    def __init__(self, database: Database):
        self.database = database

    def next_attempt_number(self, rating_job_id: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_attempt_no FROM rating_attempts WHERE rating_job_id = ?",
                (rating_job_id,),
            ).fetchone()
        return int(row["next_attempt_no"])

    def add(self, attempt: RatingAttempt) -> RatingAttempt:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO rating_attempts (
                    id, rating_job_id, attempt_no, match_strategy, write_strategy, status, error_type,
                    error_message, started_at, finished_at, debug_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.id,
                    attempt.rating_job_id,
                    attempt.attempt_no,
                    attempt.match_strategy.value if attempt.match_strategy else None,
                    attempt.write_strategy.value if attempt.write_strategy else None,
                    attempt.status.value,
                    attempt.error_type.value if attempt.error_type else None,
                    attempt.error_message,
                    attempt.started_at.isoformat(),
                    attempt.finished_at.isoformat() if attempt.finished_at else None,
                    json.dumps(attempt.debug_payload),
                ),
            )
        return attempt

    def finish_success(
        self,
        attempt_id: str,
        *,
        finished_at: datetime,
        match_strategy: str,
        write_strategy: str,
    ) -> RatingAttempt:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE rating_attempts
                SET status = ?, finished_at = ?, match_strategy = ?, write_strategy = ?
                WHERE id = ?
                """,
                (
                    RatingAttemptStatus.SUCCEEDED.value,
                    finished_at.isoformat(),
                    match_strategy,
                    write_strategy,
                    attempt_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM rating_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        return _rating_attempt_from_row(row)

    def finish_failure(
        self,
        attempt_id: str,
        *,
        finished_at: datetime,
        error_type: str,
        error_message: str,
    ) -> RatingAttempt:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE rating_attempts
                SET status = ?, finished_at = ?, error_type = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    RatingAttemptStatus.FAILED.value,
                    finished_at.isoformat(),
                    error_type,
                    error_message,
                    attempt_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM rating_attempts WHERE id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        return _rating_attempt_from_row(row)


class FilmMatchCacheRepository:
    def __init__(self, database: Database):
        self.database = database

    def get_by_tmdb_id(self, tmdb_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM film_match_cache WHERE tmdb_id = ?",
                (tmdb_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["metadata_json"] = json.loads(data["metadata_json"])
        return data

    def set_lid(self, cache_id: str, lid: str) -> None:
        """Backfill the base-62 LID on a row cached before the API migration."""
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE film_match_cache SET letterboxd_lid = ? WHERE id = ?",
                (lid, cache_id),
            )


class NotificationRepository:
    def __init__(self, database: Database):
        self.database = database

    def add(self, record: NotificationRecord) -> NotificationRecord:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO notifications (
                    id, watch_event_id, discord_channel_id, discord_message_id,
                    discord_view_state, sent_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.watch_event_id,
                    record.discord_channel_id,
                    record.discord_message_id,
                    record.discord_view_state,
                    record.sent_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def get_latest_for_watch_event(self, watch_event_id: str) -> NotificationRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM notifications
                WHERE watch_event_id = ?
                ORDER BY sent_at DESC
                LIMIT 1
                """,
                (watch_event_id,),
            ).fetchone()
        return _notification_from_row(row) if row else None

    def get_by_discord_message_id(self, discord_message_id: str) -> NotificationRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM notifications WHERE discord_message_id = ? LIMIT 1",
                (discord_message_id,),
            ).fetchone()
        return _notification_from_row(row) if row else None

    def update_view_state(self, notification_id: str, discord_view_state: str, updated_at: datetime) -> NotificationRecord:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE notifications
                SET discord_view_state = ?, updated_at = ?
                WHERE id = ?
                """,
                (discord_view_state, updated_at.isoformat(), notification_id),
            )
            row = connection.execute(
                "SELECT * FROM notifications WHERE id = ?",
                (notification_id,),
            ).fetchone()
        if row is None:
            raise KeyError(notification_id)
        return _notification_from_row(row)


def _watch_event_from_row(row) -> WatchEvent:
    return WatchEvent(
        id=row["id"],
        plex_rating_key=row["plex_rating_key"],
        plex_guid_hash=row["plex_guid_hash"],
        tmdb_id=row["tmdb_id"],
        title=row["title"],
        original_title=row["original_title"],
        year=row["year"],
        watched_at=datetime.fromisoformat(row["watched_at"]),
        view_count_at_watch=row["view_count_at_watch"],
        library_name=row["library_name"],
        raw_payload=json.loads(row["raw_payload_json"]),
        detected_at=datetime.fromisoformat(row["detected_at"]),
    )


def _rating_job_from_row(row) -> RatingJob:
    return RatingJob(
        id=row["id"],
        watch_event_id=row["watch_event_id"],
        notification_id=row["notification_id"],
        requested_rating=row["requested_rating"],
        requested_liked=bool(row["requested_liked"]),
        requested_rewatch=bool(row["requested_rewatch"]),
        requested_by_discord_user_id=row["requested_by_discord_user_id"],
        status=RatingJobStatus(row["status"]),
        job_locked_at=_dt(row["job_locked_at"]),
        job_lock_owner=row["job_lock_owner"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        requested_tags=_tags_from_row(row),
        requested_review=_column(row, "requested_review") or "",
    )


def _tags_from_row(row) -> tuple[str, ...]:
    raw = _column(row, "requested_tags")
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(tag) for tag in parsed if str(tag).strip())


def _column(row, name: str):
    """Read a column that may be absent when a row comes from a stubbed source."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _rating_attempt_from_row(row) -> RatingAttempt:
    return RatingAttempt(
        id=row["id"],
        rating_job_id=row["rating_job_id"],
        attempt_no=row["attempt_no"],
        match_strategy=row["match_strategy"],
        write_strategy=row["write_strategy"],
        status=RatingAttemptStatus(row["status"]),
        error_type=row["error_type"],
        error_message=row["error_message"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=_dt(row["finished_at"]),
        debug_payload=json.loads(row["debug_payload_json"]),
    )


def _rating_result_from_row(row) -> RatingResult:
    return RatingResult(
        id=row["id"],
        watch_event_id=row["watch_event_id"],
        rating_job_id=row["rating_job_id"],
        rating_attempt_id=row["rating_attempt_id"],
        letterboxd_film_id=row["letterboxd_film_id"],
        letterboxd_entry_id=row["letterboxd_entry_id"],
        rating_value=row["rating_value"],
        liked=bool(row["liked"]),
        rewatch=bool(row["rewatch"]),
        watched_on=_date(row["watched_on"]),
        succeeded_at=datetime.fromisoformat(row["succeeded_at"]),
    )


def _notification_from_row(row) -> NotificationRecord:
    return NotificationRecord(
        id=row["id"],
        watch_event_id=row["watch_event_id"],
        discord_channel_id=row["discord_channel_id"],
        discord_message_id=row["discord_message_id"],
        discord_view_state=row["discord_view_state"],
        sent_at=datetime.fromisoformat(row["sent_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
