from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from plexboxd.domain.enums import RatingAttemptStatus
from plexboxd.domain.models import RatingAttempt, RatingJob, RatingResult, WatchEvent
from plexboxd.integrations.letterboxd.session import AuthenticationError, CloudflareChallengeError, LetterboxdSessionError


@dataclass(slots=True)
class RatingExecutionService:
    attempt_repository: object
    result_repository: object
    matcher: object
    writer: object
    verifier: object
    id_factory: object
    clock: object

    def execute(self, job: RatingJob, event: WatchEvent) -> RatingResult:
        if self.result_repository.exists_for_watch_event(event.id):
            return self.result_repository.get_by_watch_event(event.id)

        started_at = self.clock.now()
        attempt = RatingAttempt(
            id=self.id_factory.new("attempt"),
            rating_job_id=job.id,
            attempt_no=self.attempt_repository.next_attempt_number(job.id),
            status=RatingAttemptStatus.RUNNING,
            started_at=started_at,
        )
        attempt = self.attempt_repository.add(attempt)

        try:
            match = self.matcher.resolve(event)
            if match is None:
                failed = self.attempt_repository.finish_failure(
                    attempt.id,
                    finished_at=self.clock.now(),
                    error_type="match_not_found",
                    error_message="No Letterboxd match candidates found",
                )
                raise RuntimeError(failed.error_message)

            write_result = self.writer.write(
                letterboxd_film_id=match.letterboxd_film_id,
                letterboxd_slug=match.letterboxd_slug,
                letterboxd_lid=getattr(match, "letterboxd_lid", None),
                rating=job.requested_rating,
                liked=job.requested_liked,
                rewatch=job.requested_rewatch,
                watched_on=event.watched_at.date(),
                tags=job.requested_tags,
                review=job.requested_review,
            )

            self.verifier.verify(write_result=write_result, event=event, job=job)
        except Exception as exc:
            self.attempt_repository.finish_failure(
                attempt.id,
                finished_at=self.clock.now(),
                error_type=_classify_error_type(exc),
                error_message=str(exc),
            )
            raise

        finished_at = self.clock.now()
        self.attempt_repository.finish_success(
            attempt.id,
            finished_at=finished_at,
            match_strategy=match.strategy.value,
            write_strategy=write_result["write_strategy"],
        )
        result = RatingResult(
            id=self.id_factory.new("result"),
            watch_event_id=event.id,
            rating_job_id=job.id,
            rating_attempt_id=attempt.id,
            letterboxd_film_id=match.letterboxd_film_id,
            letterboxd_entry_id=write_result.get("letterboxd_entry_id"),
            rating_value=job.requested_rating,
            liked=job.requested_liked,
            rewatch=job.requested_rewatch,
            watched_on=_determine_watched_on(event, write_result),
            succeeded_at=finished_at,
        )
        self.result_repository.add(result)
        return result


def _determine_watched_on(event: WatchEvent, write_result: dict) -> date:
    """Record the date Letterboxd actually stored, not the raw watch timestamp.

    The writer shifts the date back a day for watches before DATE_THRESHOLD_HOUR, so
    using the event date here would disagree with the diary entry.
    """
    written = write_result.get("watched_on") if write_result else None
    if written:
        try:
            return date.fromisoformat(str(written))
        except ValueError:
            pass
    return event.watched_at.date()


def _classify_error_type(exc: Exception) -> str:
    if isinstance(exc, CloudflareChallengeError):
        return "challenge_detected"
    if isinstance(exc, AuthenticationError):
        return "auth_failed"
    if isinstance(exc, LetterboxdSessionError):
        return "write_rejected"
    return "unknown"
