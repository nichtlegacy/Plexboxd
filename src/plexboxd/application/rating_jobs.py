from __future__ import annotations

from dataclasses import dataclass

from plexboxd.domain.enums import RatingJobStatus
from plexboxd.domain.models import RatingJob, RatingRequest


class RatingJobAlreadyCompletedError(Exception):
    """Raised when a successful result already exists for a watch event."""


@dataclass(slots=True)
class RatingJobService:
    repository: object
    result_repository: object
    id_factory: object
    clock: object

    def enqueue(self, watch_event_id: str, notification_id: str | None, request: RatingRequest) -> RatingJob:
        if self.result_repository.exists_for_watch_event(watch_event_id):
            raise RatingJobAlreadyCompletedError(
                f"watch_event '{watch_event_id}' already has a successful rating result"
            )

        existing_job = self.repository.get_active_for_watch_event(watch_event_id)
        if existing_job is not None:
            return existing_job

        now = self.clock.now()
        job = RatingJob(
            id=self.id_factory.new("job"),
            watch_event_id=watch_event_id,
            notification_id=notification_id,
            requested_rating=request.rating,
            requested_liked=request.liked,
            requested_rewatch=request.rewatch,
            requested_by_discord_user_id=request.requested_by_discord_user_id,
            status=RatingJobStatus.PENDING,
            created_at=now,
            updated_at=now,
            requested_tags=tuple(request.tags),
            requested_review=request.review,
        )
        return self.repository.add(job)

    def mark_status(self, job: RatingJob, new_status: RatingJobStatus) -> RatingJob:
        if not job.can_transition_to(new_status):
            raise ValueError(f"cannot transition rating job from {job.status} to {new_status}")
        updated_at = self.clock.now()
        return self.repository.update_status(job.id, new_status, updated_at)

    def claim_next(self, worker_id: str) -> RatingJob | None:
        now = self.clock.now()
        return self.repository.claim_next(worker_id=worker_id, locked_at=now)
